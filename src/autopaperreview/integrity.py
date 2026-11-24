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


_PERCENTAGE = r"(?<![\w.,+−-])(?:\d+(?:\.\d+)?|\.\d+)\s*%(?!\w)"
_PERCENT = re.compile(_PERCENTAGE)
_ACCURACY = r"(?:(?:test|validation|classification|overall|top-1)\s+)*accuracy"
_ACCURACY_FIELD = re.compile(
    rf"^(?:(?:our|the)\s+)?{_ACCURACY}\s*"
    rf"(?:(?:is|was|of)\s+|[:=]\s*|observed on [^,;:%]{{1,80}}?\s+(?:is|was)\s+)?{_PERCENTAGE}",
    re.IGNORECASE,
)
_ACCURACY_REPORT = re.compile(
    rf"^(?:we|(?:our|the proposed)\s+(?:model|method|system|classifier|detector))\s+"
    rf"(?:achiev(?:e|es|ed)|attain(?:s|ed)?|obtain(?:s|ed)?|report(?:s|ed)?|reach(?:es|ed)?)\s+"
    rf"(?:an?\s+)?(?:{_ACCURACY}\s+of\s+{_PERCENTAGE}|{_PERCENTAGE}\s+{_ACCURACY}\b)",
    re.IGNORECASE,
)
_LINKED_ACCURACY = re.compile(
    rf"{_PERCENTAGE}\s+{_ACCURACY}\b|\b{_ACCURACY}\s*(?:(?:is|was|of)\s+|[:=]\s*)?{_PERCENTAGE}",
    re.IGNORECASE,
)
_NON_RESULT = re.compile(
    r"\b(?:baseline|target|threshold|improvement|increase|decrease|gain|loss|"
    r"higher|lower|relative|interval|approximately|roughly|at least|at most)\b", re.IGNORECASE
)
_FIXTURE_ARXIV = re.compile(r"^0+\.0+$")
_SENTENCE = re.compile(r"(?<=[.!?。？！])\s+")
_REPORTING_TERM = re.compile(
    r"\b(?:confidence\s+intervals?|standard\s+(?:errors?|deviations?)|error\s+bars?|"
    r"(?:random|repeated)\s+seeds?|independent\s+runs?|single\s+run)\b",
    re.IGNORECASE,
)
_BIBLIOGRAPHY = re.compile(r"(?im)^[ \t#]*(?:\d+\.?[ \t]+)?(?:references|bibliography)[ \t]*\r?$")
_APPENDIX = re.compile(r"(?im)^[ \t#]*(?:appendix|appendices|supplementary material)\b[^\n]{0,100}$")


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


def _reporting_pointer(text: str) -> IntegrityRecord:
    """Point to a manuscript passage. A keyword cannot determine scientific adequacy."""
    references = _BIBLIOGRAPHY.search(text)
    section_start = 0
    end = references.start() if references else len(text)
    match = _REPORTING_TERM.search(text, 0, end)
    if match is None and references:
        appendix = _APPENDIX.search(text, references.end())
        if appendix:
            section_start = appendix.start()
            end = len(text)
            match = _REPORTING_TERM.search(text, section_start)
    if match is None:
        return IntegrityRecord(
            id="INT-REPRO-reporting",
            kind=IntegrityKind.reproducibility_attestation,
            verdict=IntegrityVerdict.unclear,
            subject="Uncertainty reporting",
            notes=LocalizedText(
                primary="The automatic text check found no passage to assess.",
                language="en",
                translations={"zh-Hans": "自动文本检查未找到可核查的段落。"},
            ),
        )

    # Save original character offsets. Bound the context even for unwrapped text.
    start = max(section_start, match.start() - 100)
    paragraph_start = text.rfind("\n\n", start, match.start())
    if paragraph_start >= 0:
        start = paragraph_start + 2
    end = min(end, match.end() + 100)
    paragraph_break = text.find("\n\n", match.end(), end)
    if paragraph_break >= 0:
        end = paragraph_break
    excerpt = text[start:end]
    term = " ".join(match.group().split())
    return IntegrityRecord(
        id="INT-REPRO-reporting",
        kind=IntegrityKind.reproducibility_attestation,
        verdict=IntegrityVerdict.unclear,
        subject=f"“{term}” · manuscript[{start}:{end}]",
        observed=excerpt,
        notes=LocalizedText(
            primary="For an empirical claim that depends on variation, check which independent units or repeated runs this passage describes.",
            language="en",
            translations={"zh-Hans": "若实证结论依赖样本或运行间的波动，请核对该处涉及哪些独立单位或重复运行。"},
        ),
        metadata={"source_start": start, "source_end": end},
    )


def _manuscript_accuracy(text: str) -> tuple[float | None, bool]:
    """Bind an unqualified results.json scalar only to a single, explicit accuracy result."""
    values: set[float] = set()
    other_values: set[float] = set()
    for segment in re.split(r"(?<=[.!?])\s+|[;\n]", text):
        segment = segment.strip()
        if not (_ACCURACY_FIELD.match(segment) or _ACCURACY_REPORT.match(segment)):
            for match in _LINKED_ACCURACY.finditer(segment):
                percentage = _PERCENT.search(match.group())
                other_values.add(float(percentage.group().removesuffix("%").strip()) / 100)
            continue
        if _NON_RESULT.search(segment) or re.search(r"%\s*(?:[±+−–-]|to\b)", segment):
            continue
        percentages = _PERCENT.findall(segment)
        if len(percentages) != 1:
            return None, True
        value = float(percentages[0].removesuffix("%").strip())
        if 0 <= value <= 100:
            values.add(value / 100)
    if values and len(values | other_values) > 1:
        return None, True
    return (values.pop() if values else None), False


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
    reporting_pointer: bool = False,
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

    expected, ambiguous = _manuscript_accuracy(manuscript_text)
    results_files = [item for item in inventory if Path(item.path).name == "results.json"]
    if ambiguous or (expected is not None and len(results_files) > 1):
        records.append(
            IntegrityRecord(
                id="INT-RES-accuracy",
                kind=IntegrityKind.results_integrity,
                verdict=IntegrityVerdict.unclear,
                subject="results.json / accuracy",
                notes=LocalizedText(
                    primary="The accuracy field cannot be linked to a unique manuscript measurement and results file.",
                    language="en",
                    translations={"zh-Hans": "无法将准确率字段唯一对应到稿件中的测量值和工作区中的结果文件。"},
                ),
            )
        )
    elif expected is not None and results_files:
        results_artifact = results_files[0]
        payload = _load_json_artifact(results_artifact, workspace) or {}
        observed = payload.get("accuracy")
        if type(observed) in (int, float) and 0 <= observed <= 1:
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
    elif expected is not None:
        records.append(
            IntegrityRecord(
                id="INT-RES-accuracy",
                kind=IntegrityKind.results_integrity,
                verdict=IntegrityVerdict.missing,
                subject="manuscript accuracy claim",
                expected=str(expected),
                notes=_localized("No results.json was present in the inspected workspace."),
                metadata={"note_code": "no-results"},
            )
        )

    if reporting_pointer:
        records.append(_reporting_pointer(manuscript_text))
        return records

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
