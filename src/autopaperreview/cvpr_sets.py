"""CVPR tier set registry, seal, and lock.

The committed registry stores draw parameters and id-list hashes. Sealed id
lists are not copied here. ``fetch`` of a sealed set is refused. ``lock.json``
is hashes and configuration.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .hashing import hash_json, sha256_bytes, sha256_file

PROTOCOL_ID = "cvpr-tier-lock-v1"
PROTOCOL_DATE = "2026-10-29"
ROLES = ("dev", "confirm", "reserve", "probe", "control")
STATUSES = ("fresh", "dev", "sealed", "opened")
TRANSITIONS = {
    "fresh": {"dev", "sealed"},
    "dev": set(),
    "sealed": {"opened"},
    "opened": set(),
}


class SetError(ValueError):
    """The set registry or the lock cannot be applied."""


class SetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: str
    year: int
    quotas: dict[str, int]
    seed: int
    exclude: list[str] = Field(default_factory=list)
    status: str
    id_list_sha256: str | None = None
    id_list_source: str | None = None
    expect_available: dict[str, int] | None = None
    notes: str = ""


class SetRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    protocol: str = PROTOCOL_ID
    sets: list[SetSpec]


def registry_path() -> Path:
    from .public_calibration import repo_root

    return repo_root() / "benchmarks" / "cvpr_tier" / "sets.json"


def id_list_digest(ids: list[str] | tuple[str, ...]) -> str:
    """SHA-256 of the sorted unique id list. The digest does not reveal the ids."""
    payload = ("\n".join(sorted(set(ids))) + "\n").encode("utf-8")
    return sha256_bytes(payload)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_registry_file(path: Path) -> SetRegistry:
    try:
        registry = SetRegistry.model_validate(_read_json(path))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise SetError(f"set registry is invalid: {exc}") from exc
    seen: set[str] = set()
    for spec in registry.sets:
        if spec.id in seen:
            raise SetError(f"duplicate set id {spec.id}")
        seen.add(spec.id)
        if spec.role not in ROLES:
            raise SetError(f"{spec.id} role must be one of {', '.join(ROLES)}")
        if spec.status not in STATUSES:
            raise SetError(f"{spec.id} status must be one of {', '.join(STATUSES)}")
        for tier in ("oral", "highlight", "poster"):
            if tier not in spec.quotas:
                raise SetError(f"{spec.id} quotas are missing {tier}")
            if int(spec.quotas[tier]) < 0:
                raise SetError(f"{spec.id} quota for {tier} is negative")
        if spec.id_list_sha256 is not None and len(spec.id_list_sha256) != 64:
            raise SetError(f"{spec.id} id list hash must be 64 hex characters")
    return registry


def builtin_registry() -> SetRegistry:
    return load_registry_file(registry_path())


def ensure_cache_registry(cache: Path) -> SetRegistry:
    path = cache / "sets.json"
    if not path.is_file():
        path.write_text(registry_path().read_text(encoding="utf-8"), encoding="utf-8")
    return load_registry_file(path)


def save_cache_registry(cache: Path, registry: SetRegistry) -> None:
    _write_json(cache / "sets.json", registry.model_dump())


def find_set(registry: SetRegistry, set_id: str) -> SetSpec:
    for spec in registry.sets:
        if spec.id == set_id:
            return spec
    raise SetError(f"unknown set {set_id}")


def transition(spec: SetSpec, status: str) -> None:
    if status not in TRANSITIONS.get(spec.status, set()):
        raise SetError(f"{spec.id} cannot move from {spec.status} to {status}")
    spec.status = status


def load_source_ids(source: str) -> list[str]:
    """Read an id list already stored outside the registry. Callers must not print it."""
    from .public_calibration import repo_root

    path_part, _, fragment = source.partition("#")
    path = repo_root() / path_part
    if not path.is_file():
        raise SetError("id list source is missing")
    node: Any = _read_json(path)
    for part in fragment.split("."):
        if not part:
            continue
        if not isinstance(node, dict) or part not in node:
            raise SetError("id list source fragment does not resolve")
        node = node[part]
    if not isinstance(node, list) or not all(isinstance(item, str) for item in node):
        raise SetError("id list source is not a list of ids")
    return list(node)


def resolve_member_ids(cache: Path, spec: SetSpec) -> list[str] | None:
    """Ids for a set that already has a source or a cache manifest. None when undrawn."""
    source_ids = load_source_ids(spec.id_list_source) if spec.id_list_source else None
    manifest = cache / "sets" / spec.id / "sample_manifest.json"
    cache_ids = None
    if manifest.is_file():
        payload = _read_json(manifest)
        papers = payload.get("papers") if isinstance(payload, dict) else None
        if not isinstance(papers, list):
            raise SetError(f"{spec.id} manifest has no papers")
        cache_ids = [str(item["paper_id"]) for item in papers if isinstance(item, dict) and item.get("paper_id")]
    if source_ids is not None and cache_ids is not None and set(source_ids) != set(cache_ids):
        raise SetError(f"{spec.id} cache manifest does not match the registered id source")
    ids = source_ids if source_ids is not None else cache_ids
    if ids is not None and spec.id_list_sha256:
        digest = id_list_digest(ids)
        if digest != spec.id_list_sha256:
            raise SetError(f"{spec.id} id list does not match the registered hash")
    return ids


def exclusion_ids(cache: Path, registry: SetRegistry, spec: SetSpec, extra: list[str]) -> set[str]:
    blocked: set[str] = set()
    missing: list[str] = []
    for set_id in [*spec.exclude, *extra]:
        other = find_set(registry, set_id)
        ids = resolve_member_ids(cache, other)
        if not ids:
            missing.append(set_id)
            continue
        blocked.update(ids)
    if missing:
        raise SetError("exclusion set has no id list: " + ", ".join(missing))
    return blocked


def refuse_sealed_fetch(spec: SetSpec) -> None:
    if spec.status in {"sealed", "opened"}:
        raise SetError(f"{spec.id} is {spec.status}; refusing to fetch")


def tool_version(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        return "unavailable"
    try:
        completed = subprocess.run(
            [binary, "-v"],
            check=False,
            capture_output=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    text = (completed.stdout or completed.stderr).decode("utf-8", "replace").strip()
    return (text.splitlines()[0] if text else "unavailable")[:200]


def git_commit() -> str:
    from .public_calibration import repo_root

    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo_root()), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return "unknown"
    return output.decode("utf-8", "replace").strip() or "unknown"


def _task_hashes(mode: str) -> tuple[str, str]:
    from .cvpr_tier import cvpr_tier_instructions, mode_task_text

    task = sha256_bytes(mode_task_text(mode).encode("utf-8"))
    tier = sha256_bytes(cvpr_tier_instructions().encode("utf-8"))
    return task, tier


def build_lock(
    registry: SetRegistry | None = None,
    *,
    holistic_mode: str = "forecast",
    holistic_samples: int = 1,
    label_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Configuration and hashes. Paper ids and manuscript text are not included."""
    from .cvpr_anonymize import ANONYMIZER_VERSION, PREAMBLE_CHARS, TITLE_NGRAM, anonymizer_sha256

    registry = registry or builtin_registry()
    task_sha, tier_sha = _task_hashes(holistic_mode)
    labels = label_hashes or {}
    sets: dict[str, Any] = {}
    for spec in registry.sets:
        sets[spec.id] = {
            "exclude": list(spec.exclude),
            "expect_available": spec.expect_available,
            "id": spec.id,
            "id_list_sha256": spec.id_list_sha256,
            "labels_sha256": labels.get(spec.id),
            "quotas": dict(spec.quotas),
            "role": spec.role,
            "seed": spec.seed,
            "status": spec.status,
            "year": spec.year,
        }
    body: dict[str, Any] = {
        "analysis": {
            "alpha": 0.025,
            "correction": "holm",
            "goal": "G1",
            "iclr_lock": "forecast-k1",
            "lock_id": PROTOCOL_ID,
            "primary": "macro-AUC",
            "separate_from_iclr": True,
            "tau": 0.55,
        },
        "anonymizer": {
            "sha256": anonymizer_sha256(),
            "version": ANONYMIZER_VERSION,
        },
        "date": PROTOCOL_DATE,
        "decisions": {
            "Q1": "macro-AUC, tau 0.55, Holm across three models",
            "Q2": "2024 confirmation extends with S24-C2",
            "Q3": "2026 label frame is later work, only if the source terms allow it",
            "Q4": "2025 highlight labels are not built by hand",
            "Q5": "Grok uses the local CLI through the command backend. No xAI API and no API key.",
            "Q6": "Opus and Astra use local CLIs, stdin, and a recorded version string",
            "Q7": "G1, a submission-shaped review score",
            "Q8": "CVPR lock is separate from the ICLR forecast-k1 lock",
            "Q9": "k stays 1 until a retest says otherwise",
            "Q10": "mixed budget; this file does not start a run",
            "Q11": "paraphrase model is outside this lock",
            "Q12": "A1 is the default input. A2 is diagnostic",
            "Q13": "sealed id lists are stored as hashes until the set is opened",
            "Q14": "ICLR holdout contamination is a later check",
            "Q15": "conclusions stay conditional on measured familiarity",
            "Q16": "macro-AUC above 0.8 is not a target",
        },
        "harness_git_commit": git_commit(),
        "leak_scan": {
            "author_surname_max": 0,
            "preamble_chars": PREAMBLE_CHARS,
            "residual_url_max": 0,
            "tier_phrase_max": 0,
            "title_ngram": TITLE_NGRAM,
            "venue_marker_max": 0,
        },
        "models": {
            "astra": {
                "backend": "command",
                "label": "astra-local",
                "transport": "local-cli-stdin",
            },
            "grok": {
                "backend": "command",
                "label": "grok-4.7-cursor",
                "transport": "local-cli-stdin",
                "xai_api": False,
            },
            "opus": {
                "backend": "command",
                "label": "opus-local",
                "transport": "local-cli-stdin",
            },
        },
        "pdftotext": tool_version("pdftotext"),
        "protocol": PROTOCOL_ID,
        "python": platform.python_version(),
        "sets": sets,
        "task": {
            "aggregation": "mean-of-mode-rank",
            "holistic_mode": holistic_mode,
            "k": holistic_samples,
            "rank_source": "forecast_mean" if holistic_mode == "forecast" else "mode-rank",
            "retry_max": 1,
            "retry_on": ["parse_failure", "timeout"],
            "task_sha256": task_sha,
            "tier_instructions_sha256": tier_sha,
        },
    }
    body["sha256"] = hash_json({key: value for key, value in body.items() if key != "sha256"})
    return body


def write_lock(path: Path, lock: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(lock)
    if "cvpr20" in blob.lower():
        raise SetError("lock.json must not contain a catalog id")
    _write_json(path, lock)
    return lock


def load_lock(path: Path) -> dict[str, Any]:
    try:
        payload = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise SetError(f"lock.json cannot be read: {exc}") from exc
    if not isinstance(payload, dict) or "sha256" not in payload:
        raise SetError("lock.json is missing sha256")
    body = {key: value for key, value in payload.items() if key != "sha256"}
    if hash_json(body) != payload["sha256"]:
        raise SetError("lock.json sha256 does not match its body")
    if payload.get("protocol") != PROTOCOL_ID:
        raise SetError("lock protocol id does not match")
    return payload


def verify_lock_for_run(
    lock: dict[str, Any],
    spec: SetSpec,
    *,
    holistic_mode: str,
    holistic_samples: int,
    member_ids: list[str] | None,
) -> None:
    """Check the sealed set and the locked task. A mismatch stops the run."""
    entry = (lock.get("sets") or {}).get(spec.id)
    if not isinstance(entry, dict):
        raise SetError(f"lock.json does not include {spec.id}")
    if not spec.id_list_sha256:
        raise SetError(f"{spec.id} has no id list hash to verify")
    if entry.get("id_list_sha256") != spec.id_list_sha256:
        raise SetError("lock id list hash does not match the set registry")
    if member_ids is not None and id_list_digest(member_ids) != spec.id_list_sha256:
        raise SetError("sample manifest does not match the locked id list hash")
    if int(entry.get("seed")) != int(spec.seed):
        raise SetError("lock seed does not match the set registry")
    if dict(entry.get("quotas") or {}) != dict(spec.quotas):
        raise SetError("lock quotas do not match the set registry")
    task = lock.get("task") or {}
    if task.get("holistic_mode") != holistic_mode:
        raise SetError("lock holistic mode does not match this run")
    if int(task.get("k")) != int(holistic_samples):
        raise SetError("lock sample count does not match this run")
    if int(task.get("retry_max", 0)) != 1:
        raise SetError("lock retry rule is not 1")
    task_sha, tier_sha = _task_hashes(holistic_mode)
    if task.get("task_sha256") != task_sha or task.get("tier_instructions_sha256") != tier_sha:
        raise SetError("lock task text hash does not match the harness")
    from .cvpr_anonymize import anonymizer_sha256

    anonymizer = lock.get("anonymizer") or {}
    if anonymizer.get("sha256") != anonymizer_sha256():
        raise SetError("lock anonymizer hash does not match the harness")


def verify_label_hash(lock: dict[str, Any], spec: SetSpec, labels_file: Path) -> str:
    digest = sha256_file(labels_file)
    entry = (lock.get("sets") or {}).get(spec.id) or {}
    expected = entry.get("labels_sha256")
    if expected and expected != digest:
        raise SetError("custody label hash does not match the lock")
    return digest


def opened_marker_path(custody: Path, set_id: str) -> Path:
    return custody / set_id / "opened.json"


def opened_log_path(custody: Path, set_id: str) -> Path:
    return custody / set_id / "opened.log"


def assert_not_opened(custody: Path, set_id: str) -> None:
    if opened_marker_path(custody, set_id).exists():
        raise SetError("report --confirm already opened this set")


def mark_opened(custody: Path, set_id: str, payload: dict[str, Any]) -> None:
    """Create the opened marker once and append a log line."""
    path = opened_marker_path(custody, set_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise SetError("report --confirm already opened this set") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(blob)
    line = json.dumps(
        {
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "opened",
            "lock_sha256": payload.get("lock_sha256"),
            "report_sha256": payload.get("report_sha256"),
            "set_id": set_id,
        },
        sort_keys=True,
    )
    log_path = opened_log_path(custody, set_id)
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(log_fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(log_fd)
