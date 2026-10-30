"""CVPR accepted-tier calibration. Report only.

The loop asks an existing review mode for its usual JSON plus a presentation
tier (reject < poster < highlight < oral). The score is that mode's rank
(``forecast_mean``, ``panel_mean``, the rebuttal final-rating mean, or
``predicted_mean``). Nothing here fits a cutoff or writes a weight back into
``rate``.

Per-paper tiers are written only under ``heldout/``. ``prepare`` and ``run``
refuse to read label files. Model-visible filenames, packet JSON, and argv use
an opaque handoff id. Grok, Opus, and Astra run as local CLIs through the
command backend. The adapter backend is refused, and no API key is read.

A model id is a free-form label (``grok-4.7-cursor``, ``opus-local``,
``astra-local``). Each label has its own run directory.
"""

from __future__ import annotations

import html
import json
import os
import random
import re
import shlex
import statistics
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .anchors import (
    FULL_TEXT_MAX_CHARS,
    HOLISTIC_MODES,
    LOCKED_HOLISTIC_TEMPERATURE,
    REBUTTAL_PANEL_SIZE,
    expected_rating,
    forecast_mean,
    panel_mean,
)
from .cvpr_anonymize import anonymize, extract_meta, leak_failed
from .cvpr_handoff import (
    HandoffError,
    assert_model_visible,
    assert_outside,
    estimated_tokens,
    export_replay_packet,
    grok_json_prompt,
    handoff_id_for,
    import_replay_answer,
    load_canaries,
    load_or_create_salt,
    run_isolated_command,
    split_usage,
)
from .cvpr_json import JsonOutputError, parse_json_output
from .hashing import sha256_bytes
from .model_exec import (
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    AuditJudgment,
    ExpertJudgment,
    ForecastJudgment,
    HolisticJudgment,
    MatchedJudgment,
    PanelJudgment,
    RebuttalJudgment,
    audit_task_text,
    expert_task_text,
    forecast_task_text,
    matched_task_text,
    panel_task_text,
    rebuttal_task_text,
)
from .model_exec import HOLISTIC_TASK
from .public_calibration import (
    SPREAD_STDEV_FLOOR,
    SPREAD_UNIQUE_FLOOR,
    assert_local_only,
    extract_pdf_text,
    roc_auc,
    spearman,
    strip_venue_status_lines,
)

USER_AGENT = "AutoPaperReview-cvpr-tier"
DEFAULT_YEARS = (2024, 2025)
DEFAULT_SEED = 20261029
DEFAULT_N_PER_TIER = 20
DEFAULT_BOOTSTRAP_SEED = 20261029
DEFAULT_BOOTSTRAP_RESAMPLES = 4000
ACCEPTED_TIERS = ("oral", "highlight", "poster")
PREDICTED_TIERS = ("reject", "poster", "highlight", "oral")
TIER_RANK = {"poster": 1, "highlight": 2, "oral": 3}
PAIRWISE = (
    ("oral_vs_poster", "oral", "poster"),
    ("highlight_vs_poster", "highlight", "poster"),
    ("oral_vs_highlight", "oral", "highlight"),
)
LABEL_FILES = frozenset({"catalog.json", "heldout/tiers.json"})
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_OPENACCESS_HOST = "openaccess.thecvf.com"
_TITLE_BLOCK = re.compile(
    r'<dt class="ptitle">(.*?)</dt>\s*<dd>.*?</dd>\s*<dd>\s*\[<a href="([^"]+\.pdf)">pdf</a>',
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR = re.compile(r"<a [^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_TIER_LINE = re.compile(
    r"(?i)^(oral|highlight|poster)(?:\s+(presentation|paper|session))?$"
)
_CVPR_STAMP = re.compile(r"(?i)^cvpr\s+20\d{2}\b")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_SECRET_IN_TEMPLATE = re.compile(r"(?i)(api[_-]?key|secret|bearer|\bsk-[a-z0-9])")
COMMAND_PLACEHOLDERS = ("prompt_file", "answer_file", "model", "paper_id", "handoff_id", "mode")
_HIDDEN_DIRS = frozenset({"heldout", "confirmation", "custody", "probe_keys", "meta"})
_HIDDEN_NAMES = frozenset(
    {"catalog.json", "canaries.txt", "answer_key.json", "probe_answers.json"}
)

Getter = Callable[[str], bytes]

YEAR_NOTES: dict[int, str] = {
    2023: (
        "CVPR 2023 virtual JSON (cvpr-2023-orals-posters.json) sets decision to Accept "
        "and eventtype to Poster on every row. It does not publish oral or highlight labels. "
        "OpenReview notes are not used."
    ),
    2024: (
        "CVPR 2024 virtual JSON decision is Accept: Oral, Accept: Poster (Highlight), or "
        "Accept: Poster. uid collapses the oral talk and the poster slot of one paper. "
        "PDFs come from paper_pdf_url when it is on CVF Open Access, otherwise from an exact "
        "title join to the CVPR 2024 Open Access index. A partial join leaves that paper out "
        "of the sample."
    ),
    2025: (
        "CVPR 2025 virtual JSON eventtype is Oral or Poster and decision is empty. "
        "An oral paper also has a poster slot. Highlight is not a field in this file. "
        "The main-conference program guide marks highlights, but a two-column text extract "
        "does not join them reliably, and an unauthenticated OpenReview notes request is "
        "forbidden. Non-oral rows stay unlabeled so they are not sampled as posters."
    ),
    2026: (
        "The CVPR 2026 static export is a partial page. Its next link is an API that "
        "returned 403 to this client. A partial page is not a sampling frame."
    ),
}


class CvprTierError(ValueError):
    """The CVPR tier loop cannot continue. No score formula is changed."""


class CatalogPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    year: int
    tier: str | None = None
    pdf_url: str = ""
    source: str = "virtual-json"


class YearSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int
    virtual_json: str
    openaccess: str
    complete: bool
    label_field: str
    tiers: dict[str, int]
    unlabeled: int
    with_pdf: int
    notes: str


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    papers: list[CatalogPaper]
    years: list[YearSummary]


class SamplePaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    year: int
    pdf_url: str
    source: str = "virtual-json"


class ProbeExtraction(BaseModel):
    """Papers the probe could not turn into an abstract. Counts and ids only."""

    model_config = ConfigDict(extra="forbid")

    attempted: int = 0
    failed: int = 0
    failure_rate: float = 0.0
    paper_ids: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)


class SampleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    n_per_tier: dict[str, int]
    years: list[int]
    draws: dict[str, dict[str, int]]
    sources: list[YearSummary]
    papers: list[SamplePaper]
    probe_extraction: ProbeExtraction | None = None


class HeldoutTiers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    papers: dict[str, str] = Field(default_factory=dict)


def virtual_json_url(year: int) -> str:
    return f"https://cvpr.thecvf.com/static/virtual/data/cvpr-{year}-orals-posters.json"


def openaccess_index_url(year: int) -> str:
    return f"https://openaccess.thecvf.com/CVPR{year}?day=all"


def normalize_title(value: str) -> str:
    text = html.unescape(_TAG.sub(" ", value))
    text = text.lower().replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def paper_id_for(year: int, uid: object) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(uid or ""))
    if not safe:
        raise CvprTierError("a virtual-json row is missing uid")
    return f"cvpr{int(year)}-{safe}"


def parse_model_id(value: str) -> str:
    model_id = str(value or "").strip()
    if not _MODEL_ID.fullmatch(model_id):
        raise CvprTierError(
            "model id must be a free-form label of letters, digits, dots, underscores, "
            "and hyphens, such as grok-4.7-cursor, opus-local, or astra-local"
        )
    return model_id


def _openaccess_pdf(url: object) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        raw = f"https://{_OPENACCESS_HOST}{raw}"
    if raw.startswith("fixture://"):
        return raw
    parsed = urllib.parse.urlparse(raw)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != _OPENACCESS_HOST or not parsed.path.lower().endswith(".pdf"):
        return ""
    return urllib.parse.urlunparse(("https", _OPENACCESS_HOST, parsed.path, "", "", ""))


def _usable_pdf(url: str) -> bool:
    return bool(url) and (url.startswith(f"https://{_OPENACCESS_HOST}/") or url.startswith("fixture://"))


def parse_openaccess_index(page: str) -> dict[str, str]:
    """Map a normalized title to one Open Access PDF path. Ambiguous titles are dropped."""
    found: dict[str, list[str]] = defaultdict(list)
    for title_html, href in _TITLE_BLOCK.findall(page):
        anchor = _ANCHOR.search(title_html)
        title = anchor.group(1) if anchor else title_html
        pdf = _openaccess_pdf(href)
        key = normalize_title(title)
        if key and pdf:
            found[key].append(pdf)
    return {key: urls[0] for key, urls in found.items() if len(set(urls)) == 1}


def _tier_from_decision(value: object) -> str | None:
    text = str(value or "").lower()
    if "oral" in text:
        return "oral"
    if "highlight" in text:
        return "highlight"
    if "poster" in text:
        return "poster"
    return None


def _tier_from_eventtype(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if text == "oral":
        return "oral"
    if text == "poster":
        return "poster"
    return None


def _as_results(payload: Any) -> tuple[list[dict[str, Any]], bool]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], True
    if not isinstance(payload, dict):
        raise CvprTierError("virtual JSON must be an object or a list")
    results = payload.get("results")
    if not isinstance(results, list):
        raise CvprTierError("virtual JSON is missing results")
    rows = [item for item in results if isinstance(item, dict)]
    count = payload.get("count")
    complete = not payload.get("next")
    if isinstance(count, int) and count > len(rows):
        complete = False
    return rows, complete


def build_year_catalog(year: int, payload: Any, openaccess_html: str = "") -> tuple[list[CatalogPaper], YearSummary]:
    """Collapse virtual-json events to one row per paper. Titles are not returned."""
    rows, complete = _as_results(payload)
    notes = YEAR_NOTES.get(
        year,
        "No per-paper tier note is recorded for this year. Rows are unlabeled unless decision or eventtype says otherwise.",
    )
    summary = YearSummary(
        year=year,
        virtual_json=virtual_json_url(year),
        openaccess=openaccess_index_url(year),
        complete=complete,
        label_field="none",
        tiers={tier: 0 for tier in ACCEPTED_TIERS},
        unlabeled=0,
        with_pdf=0,
        notes=notes,
    )
    if not complete:
        summary.notes = notes + " This export is a partial page and is not sampled."
        return [], summary

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        uid = row.get("uid")
        if uid is None or str(uid).strip() == "":
            continue
        grouped[str(uid)].append(row)

    decision_hits = {_tier_from_decision(row.get("decision")) for group in grouped.values() for row in group}
    event_hits = {_tier_from_eventtype(row.get("eventtype") or row.get("event_type")) for group in grouped.values() for row in group}
    if "oral" in decision_hits or "highlight" in decision_hits:
        mode = "decision"
        summary.label_field = "decision"
    elif "oral" in event_hits and "poster" in event_hits:
        mode = "oral-only"
        summary.label_field = "eventtype-oral-only"
    else:
        mode = "unlabeled"
        summary.label_field = "none"

    titles = parse_openaccess_index(openaccess_html) if openaccess_html else {}
    papers: list[CatalogPaper] = []
    for uid, group in grouped.items():
        title = ""
        pdf = ""
        decision_tiers: set[str] = set()
        event_tiers: set[str] = set()
        for row in group:
            if not title and row.get("name"):
                title = str(row.get("name"))
            if not pdf:
                pdf = _openaccess_pdf(row.get("paper_pdf_url"))
            decision = _tier_from_decision(row.get("decision"))
            event = _tier_from_eventtype(row.get("eventtype") or row.get("event_type"))
            if decision:
                decision_tiers.add(decision)
            if event:
                event_tiers.add(event)
        if not pdf and title:
            pdf = titles.get(normalize_title(title), "")
        if mode == "decision":
            if "oral" in decision_tiers:
                tier: str | None = "oral"
            elif "highlight" in decision_tiers:
                tier = "highlight"
            elif "poster" in decision_tiers:
                tier = "poster"
            else:
                tier = None
        elif mode == "oral-only":
            tier = "oral" if "oral" in event_tiers else None
        else:
            tier = None
        paper = CatalogPaper(
            paper_id=paper_id_for(year, uid),
            year=year,
            tier=tier,
            pdf_url=pdf,
            source="virtual-json",
        )
        papers.append(paper)
        if tier in summary.tiers:
            summary.tiers[tier] += 1
        else:
            summary.unlabeled += 1
        if _usable_pdf(paper.pdf_url):
            summary.with_pdf += 1
    papers.sort(key=lambda item: item.paper_id)
    return papers, summary


def title_digests_for(payload: Any, openaccess_html: str = "") -> list[str]:
    """SHA-256 of normalized catalog titles. The titles themselves are not returned."""
    rows, _complete = _as_results(payload)
    digests: list[str] = []
    for row in rows:
        name = row.get("name")
        if name and str(name).strip():
            digests.append(sha256_bytes(normalize_title(str(name)).encode("utf-8")))
    if openaccess_html:
        for key in parse_openaccess_index(openaccess_html):
            if key:
                digests.append(sha256_bytes(key.encode("utf-8")))
    return digests


def strip_cvpr_banner(text: str) -> str:
    """Drop camera-ready stamps. A sentence that merely contains those words stays."""
    stripped = strip_venue_status_lines(text)
    kept: list[str] = []
    for line in stripped.splitlines():
        compact = line.strip()
        if _TIER_LINE.fullmatch(compact):
            continue
        if "open access version" in compact.lower() and len(compact) < 240:
            continue
        if _CVPR_STAMP.match(compact) and len(compact) < 80:
            continue
        kept.append(line)
    body = "\n".join(kept).strip()
    return body + ("\n" if body else "")


def cvpr_tier_instructions() -> str:
    """Ask the selected review mode for a tier. Do not state a numeric cutoff."""
    return "\n".join(
        [
            "## CVPR presentation tier",
            "Add a field named tier to the JSON object this review mode already returns.",
            "tier is one of reject, poster, highlight, oral.",
            "The order is reject < poster < highlight < oral.",
            "reject means you would not accept the paper at CVPR.",
            "poster is a typical accept.",
            "highlight is an accept you would single out.",
            "oral is an accept you would schedule as a talk.",
            "Choose the tier from the same reading that sets this mode's rank.",
            "Do not add a second score, and do not apply a numeric cutoff.",
            "The harness reads the rank this mode already defines and reads tier as a label.",
            "It does not map the rank onto a tier.",
        ]
    )


def mode_task_text(mode: str) -> str:
    if mode not in HOLISTIC_MODES:
        raise CvprTierError(f"holistic_mode must be one of {', '.join(HOLISTIC_MODES)}")
    tasks = {
        "mean": HOLISTIC_TASK,
        "expert": expert_task_text(),
        "panel": panel_task_text(),
        "matched": matched_task_text(),
        "rebuttal": rebuttal_task_text(),
        "forecast": forecast_task_text(),
        "audited": audit_task_text() + "\n\n" + HOLISTIC_TASK,
    }
    return tasks[mode]


def build_prompt(manuscript: str, mode: str, *, samples: int = 1) -> str:
    """Manuscript plus the existing mode task plus the CVPR tier field. No label is accepted here."""
    if samples < 1 or samples > 8:
        raise CvprTierError("holistic_samples must be from 1 to 8")
    body = manuscript
    cut = False
    if len(body) > FULL_TEXT_MAX_CHARS:
        body = body[:FULL_TEXT_MAX_CHARS]
        cut = True
    lines = [
        "## Manuscript",
        "",
        body.rstrip(),
        "",
    ]
    if cut:
        lines.append(f"[manuscript truncated to {FULL_TEXT_MAX_CHARS} characters]")
        lines.append("")
    lines.append(mode_task_text(mode))
    lines.append("")
    lines.append(cvpr_tier_instructions())
    if mode == "audited":
        lines.append(
            "Return one JSON object with tier, audit, and holistic. "
            "audit is the twelve-check object this mode already uses. "
            "holistic is the venue-mean object (rationale, rating, predicted_mean). "
            "The score is holistic.predicted_mean. The audit checklist is not a score."
        )
    elif samples > 1 and mode != "forecast":
        lines.append(
            f"Return tier once, plus samples: a list of {samples} objects in the shape this mode already uses. "
            "The objects are independent."
        )
    elif mode == "forecast" and samples > 1:
        lines.append(
            "Return one forecast object plus tier. Extra panels may be listed under samples. "
            "The rank uses the first panel only."
        )
    else:
        lines.append("Return the JSON object this mode already uses, with tier added.")
    return "\n".join(lines).rstrip() + "\n"


def _pop_tier(payload: dict[str, Any]) -> str:
    if "tier" not in payload:
        raise CvprTierError("model output is missing tier")
    tier = str(payload.pop("tier")).strip().lower().replace("-", " ").replace("_", " ")
    tier = re.sub(r"\s+", "_", tier)
    if tier not in PREDICTED_TIERS:
        raise CvprTierError(f"tier must be one of {', '.join(PREDICTED_TIERS)}")
    return tier


def _sample_rank(mode: str, payload: dict[str, Any]) -> tuple[float, str]:
    try:
        if mode == "forecast":
            judged = ForecastJudgment.model_validate(payload)
            expectations = [expected_rating(review.probabilities)[0] for review in judged.reviews]
            return forecast_mean(expectations), "forecast_mean"
        if mode == "panel":
            judged = PanelJudgment.model_validate(payload)
            return panel_mean([int(review.rating) for review in judged.reviews]), "panel_mean"
        if mode == "matched":
            judged = MatchedJudgment.model_validate(payload)
            return panel_mean([int(review.rating) for review in judged.reviews]), "panel_mean"
        if mode == "rebuttal":
            judged = RebuttalJudgment.model_validate(payload)
            ratings = [int(item.final_rating) for item in judged.updates]
            return panel_mean(ratings, size=REBUTTAL_PANEL_SIZE), "final_rating_mean"
        if mode == "expert":
            judged = ExpertJudgment.model_validate(payload)
            return round(float(judged.predicted_mean), 4), "predicted_mean"
        if mode == "audited":
            holistic = payload.get("holistic")
            audit = payload.get("audit")
            if not isinstance(holistic, dict) or not isinstance(audit, dict):
                raise CvprTierError("audited mode requires audit and holistic objects")
            AuditJudgment.model_validate(audit)
            judged = HolisticJudgment.model_validate(holistic)
            return round(float(judged.predicted_mean), 4), "predicted_mean"
        if mode == "mean":
            judged = HolisticJudgment.model_validate(payload)
            return round(float(judged.predicted_mean), 4), "predicted_mean"
    except (ValidationError, ValueError) as exc:
        if isinstance(exc, CvprTierError):
            raise
        raise CvprTierError(f"{mode} answer does not match that review mode: {exc}") from exc
    raise CvprTierError(f"holistic_mode must be one of {', '.join(HOLISTIC_MODES)}")


def parse_verdict(mode: str, text: str, *, samples: int = 1) -> dict[str, Any]:
    """Parse tier and the mode's own rank. The rank is not a new cutoff."""
    if mode not in HOLISTIC_MODES:
        raise CvprTierError(f"holistic_mode must be one of {', '.join(HOLISTIC_MODES)}")
    try:
        return parse_json_output(text, lambda payload: _parse_verdict_payload(mode, payload, samples=samples))
    except JsonOutputError as exc:
        raise CvprTierError(f"model output is {exc}") from exc


def _parse_verdict_payload(mode: str, value: Any, *, samples: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CvprTierError("model output is not a JSON object")
    payload = dict(value)
    tier = _pop_tier(payload)
    raw_samples = payload.pop("samples", None)
    if raw_samples is None:
        bodies = [payload]
    else:
        if not isinstance(raw_samples, list) or not raw_samples:
            raise CvprTierError("samples must be a non-empty list")
        bodies = []
        for item in raw_samples:
            if not isinstance(item, dict):
                raise CvprTierError("each sample must be an object")
            body = dict(item)
            body.pop("tier", None)
            bodies.append(body)
    ranks: list[float] = []
    source = ""
    for body in bodies:
        rank, source = _sample_rank(mode, body)
        ranks.append(rank)
    if mode in {"forecast", "audited"}:
        used = ranks[:1]
    else:
        if len(ranks) < samples:
            raise CvprTierError(f"{mode} expected {samples} samples and returned {len(ranks)}")
        used = ranks[:samples]
    score = round(sum(used) / len(used), 4)
    return {
        "score": score,
        "predicted_tier": tier,
        "rank_source": source,
        "sample_ranks": ranks,
        "rank_samples_used": len(used),
    }


def render_command(template: str, values: dict[str, str]) -> list[str]:
    """Turn a command template into argv. Values are quoted. No shell and no API key."""
    if not str(template or "").strip():
        raise CvprTierError("command backend requires a command template")
    if _SECRET_IN_TEMPLATE.search(template):
        raise CvprTierError("command template must not contain an API key, secret, or bearer token")
    unknown = [name for name in _PLACEHOLDER.findall(template) if name not in values]
    if unknown:
        raise CvprTierError(
            "command template has unknown placeholders "
            + ", ".join(sorted(set(unknown)))
            + f"; known placeholders are {', '.join(COMMAND_PLACEHOLDERS)}"
        )
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", shlex.quote(value))
    argv = shlex.split(rendered)
    if not argv:
        raise CvprTierError("command template is empty")
    return argv


def _linear_percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _cache(path: Path) -> Path:
    return assert_local_only(path)


def _relative(cache: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(cache.resolve()).as_posix()
    except ValueError:
        return ""


def _custody_root(cache: Path) -> Path | None:
    path = cache / "custody_path.txt"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return Path(text).expanduser().resolve() if text else None


def hidden_from_model(cache: Path, path: Path) -> str | None:
    """Label, custody, probe-key, and meta paths. Manuscript text is not hidden."""
    resolved = path.expanduser().resolve()
    custody = _custody_root(cache)
    if custody is not None:
        try:
            resolved.relative_to(custody)
            return "custody"
        except ValueError:
            pass
    try:
        relative = resolved.relative_to(cache.resolve())
    except ValueError:
        relative = None
    if relative is not None:
        if relative.as_posix() in _HIDDEN_NAMES or relative.name in _HIDDEN_NAMES:
            return relative.as_posix()
        if any(part in _HIDDEN_DIRS for part in relative.parts):
            return relative.as_posix()
        return None
    if any(part in _HIDDEN_DIRS for part in resolved.parts):
        return resolved.name
    return None


def read_judgment_text(cache: Path, path: Path) -> str:
    hidden = hidden_from_model(cache, path)
    if hidden:
        raise CvprTierError(f"judgment must not read {hidden}")
    return path.read_text(encoding="utf-8")


def read_judgment_bytes(cache: Path, path: Path) -> bytes:
    hidden = hidden_from_model(cache, path)
    if hidden:
        raise CvprTierError(f"judgment must not read {hidden}")
    return path.read_bytes()


def _remember_custody(cache: Path, custody: Path | None) -> None:
    if custody is None:
        return
    (cache / "custody_path.txt").write_text(str(Path(custody).expanduser().resolve()) + "\n", encoding="utf-8")


def _material_root(cache: Path, set_id: str | None) -> Path:
    return cache / "sets" / set_id if set_id else cache


def run_directory(material: Path, model_id: str, *, variant: str | None, round_id: str | None) -> Path:
    """Legacy ``runs/<model>`` when variant and round are both omitted.

    Otherwise ``runs/<model>/<variant or default>/<round or r0>``. The set id
    selects the material root (``sets/<id>``) and is not a further path segment.
    """
    if variant is None and round_id is None:
        return material / "runs" / model_id
    if round_id is not None and not _MODEL_ID.fullmatch(round_id):
        raise CvprTierError("round must be letters, digits, dot, underscore, or hyphen")
    return material / "runs" / model_id / (variant or "default") / (round_id or "r0")


def _handoff_root(cache: Path, handoff_dir: Path | None) -> Path:
    from .public_calibration import repo_root

    if handoff_dir is not None:
        root = Path(handoff_dir)
    else:
        override = os.environ.get("APR_CVPR_HANDOFF_DIR")
        if override:
            root = Path(override)
        else:
            digest = sha256_bytes(str(cache.resolve()).encode("utf-8"))[:12]
            root = Path(tempfile.gettempdir()) / "apr-handoff" / digest
    assert_outside(root, cache, repo_root(), what="handoff directory")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _reject_remote_grok(model_id: str, backend: str, base_url: str | None) -> None:
    if backend != "adapter" or "grok" not in model_id.lower():
        return
    host = (urllib.parse.urlparse(base_url or "").hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return
    raise CvprTierError(
        "Grok uses the local CLI through the command backend. A remote adapter is not used."
    )


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _wrap_tier(exc: Exception) -> CvprTierError:
    if isinstance(exc, CvprTierError):
        return exc
    return CvprTierError(str(exc))


def _default_get(url: str, *, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry = exc.headers.get("Retry-After") if exc.headers else None
            wait = 1.0
            if retry and retry.isdigit():
                wait = min(8.0, float(retry))
            import time

            time.sleep(wait)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        raise CvprTierError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise CvprTierError(f"could not fetch {url}: {exc.reason}") from exc


def _year_bytes(year: int, kind: str, *, getter: Getter | None, fixture_dir: Path | None) -> bytes:
    if fixture_dir is not None:
        name = f"cvpr-{year}-orals-posters.json" if kind == "virtual" else f"CVPR{year}.html"
        path = fixture_dir / name
        if not path.is_file():
            if kind == "openaccess":
                return b""
            raise CvprTierError(f"fixture is missing {name}")
        return path.read_bytes()
    url = virtual_json_url(year) if kind == "virtual" else openaccess_index_url(year)
    get = getter or _default_get
    return get(url)


def list_catalog(
    cache: Path,
    *,
    years: tuple[int, ...] = DEFAULT_YEARS,
    getter: Getter | None = None,
    fixture_dir: Path | None = None,
) -> Catalog:
    """Fetch tier indexes into the cache. Stdout callers should print the year summaries, not titles."""
    root = _cache(cache)
    papers: list[CatalogPaper] = []
    summaries: list[YearSummary] = []
    digests: list[str] = []
    for year in years:
        virtual = _year_bytes(year, "virtual", getter=getter, fixture_dir=fixture_dir)
        try:
            payload = json.loads(virtual.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CvprTierError(f"CVPR {year} virtual JSON is not JSON") from exc
        page = _year_bytes(year, "openaccess", getter=getter, fixture_dir=fixture_dir)
        html_text = page.decode("utf-8", "replace") if page else ""
        year_papers, summary = build_year_catalog(year, payload, html_text)
        papers.extend(year_papers)
        summaries.append(summary)
        digests.extend(title_digests_for(payload, html_text))
    catalog = Catalog(papers=papers, years=summaries)
    _write_json(root / "catalog.json", catalog.model_dump())
    _write_json(
        root / "title_digests.json",
        {"algorithm": "sha256(normalize_title)", "digests": sorted(set(digests))},
    )
    return catalog


def _n_per_tier(value: int | dict[str, int]) -> dict[str, int]:
    if isinstance(value, int):
        if value < 1:
            raise CvprTierError("n per tier must be at least 1")
        return {tier: value for tier in ACCEPTED_TIERS}
    result = {}
    for tier in ACCEPTED_TIERS:
        count = int(value.get(tier, 0))
        if count < 0:
            raise CvprTierError(f"n for {tier} must be non-negative")
        result[tier] = count
    if sum(result.values()) < 1:
        raise CvprTierError("at least one tier needs a positive sample size")
    return result


def stratified_sample(
    papers: list[CatalogPaper],
    n_per_tier: dict[str, int],
    seed: int,
) -> tuple[list[CatalogPaper], dict[str, dict[str, int]]]:
    """Stable shuffle inside each tier. Papers without a tier or a PDF are not drawn."""
    pools: dict[str, list[CatalogPaper]] = {tier: [] for tier in ACCEPTED_TIERS}
    for paper in papers:
        if paper.tier in pools and _usable_pdf(paper.pdf_url):
            pools[paper.tier].append(paper)
    selected: list[CatalogPaper] = []
    draws: dict[str, dict[str, int]] = {}
    for tier in ACCEPTED_TIERS:
        pool = sorted(pools[tier], key=lambda item: item.paper_id)
        rng = random.Random(f"{seed}:{tier}")
        rng.shuffle(pool)
        take = pool[: n_per_tier[tier]]
        selected.extend(take)
        draws[tier] = {"available": len(pool), "requested": n_per_tier[tier], "drawn": len(take)}
    selected.sort(key=lambda item: (item.year, item.paper_id))
    return selected, draws


def _store_draw(
    root: Path,
    selected: list[CatalogPaper],
    *,
    seed: int,
    quotas: dict[str, int],
    draws: dict[str, dict[str, int]],
    sources: list[YearSummary],
    getter: Getter | None,
    fixture_dir: Path | None,
) -> SampleManifest:
    """Write one manifest and its label file, then cache PDF or fixture text."""
    heldout = HeldoutTiers(
        papers={paper.paper_id: paper.tier for paper in selected if paper.tier is not None}
    )
    _write_json(root / "heldout" / "tiers.json", heldout.model_dump())
    manifest = SampleManifest(
        seed=seed,
        n_per_tier=quotas,
        years=sorted({paper.year for paper in selected}),
        draws=draws,
        sources=sources,
        papers=[
            SamplePaper(
                paper_id=paper.paper_id,
                year=paper.year,
                pdf_url=paper.pdf_url,
                source=paper.source,
            )
            for paper in selected
        ],
    )
    _write_json(root / "sample_manifest.json", manifest.model_dump())
    get = getter or _default_get
    for paper in selected:
        destination_raw = root / "raw" / f"{paper.paper_id}.txt"
        destination_pdf = root / "pdfs" / f"{paper.paper_id}.pdf"
        if paper.pdf_url.startswith("fixture://"):
            if fixture_dir is None:
                raise CvprTierError("fixture PDF urls require --fixture")
            source = fixture_dir / "raw" / f"{paper.paper_id}.txt"
            if not source.is_file():
                raise CvprTierError(f"fixture text is missing for {paper.paper_id}")
            destination_raw.parent.mkdir(parents=True, exist_ok=True)
            destination_raw.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            continue
        if destination_pdf.is_file() and destination_pdf.stat().st_size > 0:
            continue
        data = get(paper.pdf_url)
        if not data.startswith(b"%PDF"):
            raise CvprTierError(f"Open Access response for {paper.paper_id} is not a PDF")
        destination_pdf.parent.mkdir(parents=True, exist_ok=True)
        destination_pdf.write_bytes(data)
    return manifest


def _fetch_registered_set(
    cache: Path,
    set_id: str,
    *,
    exclude_sets: list[str] | None,
    getter: Getter | None,
    fixture_dir: Path | None,
) -> SampleManifest:
    from .cvpr_sets import (
        SetError,
        ensure_cache_registry,
        exclusion_ids,
        find_set,
        id_list_digest,
        refuse_sealed_fetch,
        save_cache_registry,
        transition,
    )

    root = _cache(cache)
    try:
        registry = ensure_cache_registry(root)
        spec = find_set(registry, set_id)
        refuse_sealed_fetch(spec)
        blocked = exclusion_ids(root, registry, spec, list(exclude_sets or []))
    except SetError as exc:
        raise CvprTierError(str(exc)) from exc
    catalog_path = root / "catalog.json"
    if not catalog_path.is_file():
        raise CvprTierError("catalog.json is missing; run list first")
    catalog = Catalog.model_validate(_read_json(catalog_path))
    pool = [
        paper
        for paper in catalog.papers
        if paper.year == spec.year and paper.paper_id not in blocked
    ]
    quotas = _n_per_tier(dict(spec.quotas))
    selected, draws = stratified_sample(pool, quotas, spec.seed)
    if spec.expect_available:
        for tier, expected in spec.expect_available.items():
            available = draws.get(tier, {}).get("available")
            if available != int(expected):
                raise CvprTierError(
                    f"{spec.id} {tier} pool is {available}, registry expects {int(expected)}"
                )
    digest = id_list_digest([paper.paper_id for paper in selected])
    if spec.id_list_sha256 and digest != spec.id_list_sha256:
        raise CvprTierError(f"{spec.id} draw does not match the registered id list hash")
    if not selected:
        raise CvprTierError("the sample is empty; no labeled paper has a PDF")
    manifest = _store_draw(
        root / "sets" / spec.id,
        selected,
        seed=spec.seed,
        quotas=quotas,
        draws=draws,
        sources=[year for year in catalog.years if year.year == spec.year],
        getter=getter,
        fixture_dir=fixture_dir,
    )
    if spec.id_list_sha256 is None:
        spec.id_list_sha256 = digest
    if spec.status == "fresh":
        try:
            transition(spec, "dev")
        except SetError as exc:
            raise CvprTierError(str(exc)) from exc
    save_cache_registry(root, registry)
    return manifest


def fetch_sample(
    cache: Path,
    *,
    n_per_tier: int | dict[str, int] = DEFAULT_N_PER_TIER,
    seed: int = DEFAULT_SEED,
    getter: Getter | None = None,
    fixture_dir: Path | None = None,
    set_id: str | None = None,
    exclude_sets: list[str] | None = None,
) -> SampleManifest:
    """Draw the sample and store PDFs or fixture text. Labels go only to heldout/tiers.json."""
    if set_id:
        return _fetch_registered_set(
            cache,
            set_id,
            exclude_sets=exclude_sets,
            getter=getter,
            fixture_dir=fixture_dir,
        )
    root = _cache(cache)
    catalog_path = root / "catalog.json"
    if not catalog_path.is_file():
        raise CvprTierError("catalog.json is missing; run list first")
    catalog = Catalog.model_validate(_read_json(catalog_path))
    quotas = _n_per_tier(n_per_tier)
    selected, draws = stratified_sample(catalog.papers, quotas, seed)
    if not selected:
        raise CvprTierError("the sample is empty; no labeled paper has a PDF")
    heldout = HeldoutTiers(
        papers={paper.paper_id: paper.tier for paper in selected if paper.tier is not None}
    )
    _write_json(root / "heldout" / "tiers.json", heldout.model_dump())
    manifest = SampleManifest(
        seed=seed,
        n_per_tier=quotas,
        years=sorted({paper.year for paper in selected}),
        draws=draws,
        sources=catalog.years,
        papers=[
            SamplePaper(
                paper_id=paper.paper_id,
                year=paper.year,
                pdf_url=paper.pdf_url,
                source=paper.source,
            )
            for paper in selected
        ],
    )
    _write_json(root / "sample_manifest.json", manifest.model_dump())
    get = getter or _default_get
    for paper in selected:
        destination_raw = root / "raw" / f"{paper.paper_id}.txt"
        destination_pdf = root / "pdfs" / f"{paper.paper_id}.pdf"
        if paper.pdf_url.startswith("fixture://"):
            if fixture_dir is None:
                raise CvprTierError("fixture PDF urls require --fixture")
            source = fixture_dir / "raw" / f"{paper.paper_id}.txt"
            if not source.is_file():
                raise CvprTierError(f"fixture text is missing for {paper.paper_id}")
            destination_raw.parent.mkdir(parents=True, exist_ok=True)
            destination_raw.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            continue
        if destination_pdf.is_file() and destination_pdf.stat().st_size > 0:
            continue
        data = get(paper.pdf_url)
        if not data.startswith(b"%PDF"):
            raise CvprTierError(f"Open Access response for {paper.paper_id} is not a PDF")
        destination_pdf.parent.mkdir(parents=True, exist_ok=True)
        destination_pdf.write_bytes(data)
    return manifest


def _load_sample(cache: Path, set_id: str | None = None) -> SampleManifest:
    path = _material_root(cache, set_id) / "sample_manifest.json"
    if not path.is_file():
        missing = f"sets/{set_id}/sample_manifest.json" if set_id else "sample_manifest.json"
        raise CvprTierError(f"{missing} is missing; run fetch first")
    return SampleManifest.model_validate(json.loads(read_judgment_text(cache, path)))


def _manuscript_path(cache: Path, paper_id: str, *, variant: str | None, set_id: str | None) -> Path:
    base = _material_root(cache, set_id) / "manuscripts"
    if variant:
        return base / variant / f"{paper_id}.txt"
    return base / f"{paper_id}.txt"


def prepare_manuscripts(
    cache: Path,
    *,
    extract: Callable[[Path], str] | None = None,
    variant: str | None = None,
    set_id: str | None = None,
) -> list[dict[str, Any]]:
    """Extract manuscripts. Variant A0/A1/A2 writes ``manuscripts/<variant>/``.

    The no-variant path keeps the historical A0 file at ``manuscripts/<paper id>.txt``.
    Meta (title and authors) is stored for anonymization and is not a prompt.
    Leak results are counts only.
    """
    if variant is not None and variant not in {"A0", "A1", "A2"}:
        raise CvprTierError("variant must be A0, A1, or A2")
    root = _cache(cache)
    manifest = _load_sample(root, set_id)
    material = _material_root(root, set_id)
    extractor = extract or extract_pdf_text
    rows: list[dict[str, Any]] = []
    totals = {
        "acknowledgement": 0,
        "author_surname": 0,
        "residual_url": 0,
        "tier_phrase": 0,
        "title_4gram": 0,
        "venue_marker": 0,
    }
    failed = 0
    for paper in manifest.papers:
        raw_path = material / "raw" / f"{paper.paper_id}.txt"
        pdf_path = material / "pdfs" / f"{paper.paper_id}.pdf"
        if raw_path.is_file():
            raw = read_judgment_text(root, raw_path)
        elif pdf_path.is_file():
            raw = extractor(pdf_path)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(raw, encoding="utf-8")
        else:
            raise CvprTierError(f"no text or PDF for {paper.paper_id}")
        if variant is None:
            manuscript = strip_cvpr_banner(raw)
            meta = extract_meta(raw)
            counts = None
        else:
            manuscript, meta, counts = anonymize(raw, variant)
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + int(value)
            if variant in {"A1", "A2"} and leak_failed(counts):
                failed += 1
        if not manuscript.strip():
            raise CvprTierError("manuscript text is empty")
        destination = _manuscript_path(root, paper.paper_id, variant=variant, set_id=set_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(manuscript, encoding="utf-8")
        meta_path = material / "meta" / f"{paper.paper_id}.json"
        _write_json(meta_path, meta)
        row: dict[str, Any] = {"paper_id": paper.paper_id, "characters": len(manuscript)}
        if counts is not None:
            row["leak"] = counts
        rows.append(row)
    if variant is not None:
        _write_json(
            material / "manuscripts" / variant / "leak_counts.json",
            {
                "variant": variant,
                "manuscripts": len(rows),
                "failed": failed,
                "counts": totals,
                "papers": {row["paper_id"]: row["leak"] for row in rows},
            },
        )
    if failed:
        summary = " ".join(f"{key}={totals[key]}" for key in sorted(totals))
        raise CvprTierError(f"{variant} leak scan failed for {failed} manuscripts: {summary}")
    return rows


def _run_leak_failures(
    cache: Path,
    manifest: SampleManifest,
    *,
    variant: str | None,
    set_id: str | None,
) -> dict[str, dict[str, int]]:
    if variant not in {"A1", "A2"}:
        return {}
    path = _material_root(cache, set_id) / "manuscripts" / variant / "leak_counts.json"
    message = f"{variant} per-paper leak counts are missing or invalid; rerun prepare --variant {variant}"
    try:
        payload = json.loads(read_judgment_text(cache, path))
    except (OSError, ValueError) as exc:
        raise CvprTierError(message) from exc
    if not isinstance(payload, dict) or payload.get("variant") != variant:
        raise CvprTierError(message)
    papers = payload.get("papers")
    if not isinstance(papers, dict):
        raise CvprTierError(message)
    failures: dict[str, dict[str, int]] = {}
    for paper in manifest.papers:
        counts = papers.get(paper.paper_id)
        if not isinstance(counts, dict) or not counts or any(
            type(value) is not int or value < 0 for value in counts.values()
        ):
            raise CvprTierError(message)
        if leak_failed(counts):
            failures[paper.paper_id] = {key: value for key, value in counts.items() if value > 0}
    return failures


def _prompt_for(
    cache: Path,
    paper_id: str,
    mode: str,
    samples: int,
    *,
    variant: str | None = None,
    set_id: str | None = None,
    model_id: str = "",
) -> tuple[str, str]:
    path = _manuscript_path(cache, paper_id, variant=variant, set_id=set_id)
    if not path.is_file():
        where = f"variant {variant}" if variant else "prepare"
        raise CvprTierError(f"manuscript is missing for {paper_id}; run {where} first")
    manuscript = read_judgment_text(cache, path)
    prompt = grok_json_prompt(build_prompt(manuscript, mode, samples=samples), model_id)
    return prompt, sha256_bytes(prompt.encode("utf-8"))


class OpenAIChatCompletion:
    """One chat completion. The API key is read from the environment and is not stored."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str = "APR_MODEL_API_KEY",
        allow_remote: bool = False,
        network_policy: str = "deny",
        temperature: float = LOCKED_HOLISTIC_TEMPERATURE,
        timeout_seconds: float = DEFAULT_MODEL_TIMEOUT_SECONDS,
        transport: Any = None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key_env = api_key_env
        self.allow_remote = allow_remote
        self.network_policy = network_policy
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def complete(self, prompt: str, scratch: Path) -> str:
        raise CvprTierError(
            "Models run through the local CLI command backend. "
            "The adapter backend is not used and no API key is read."
        )


def load_completion_backend(spec: str) -> Any:
    raise CvprTierError(
        "Models run through the local CLI command backend. "
        "The adapter backend is not used and no API key is read."
    )


def _enforce_sealed_run(
    cache: Path,
    set_id: str | None,
    *,
    confirm: bool,
    lock_path: Path | None,
    holistic_mode: str,
    holistic_samples: int,
    member_ids: list[str],
) -> None:
    if not set_id:
        if confirm or lock_path is not None:
            raise CvprTierError("--confirm --lock requires --set")
        return
    from .cvpr_sets import SetError, ensure_cache_registry, find_set, load_lock, verify_lock_for_run

    try:
        spec = find_set(ensure_cache_registry(cache), set_id)
        if spec.status == "opened":
            raise CvprTierError(f"{set_id} is opened; refusing to run")
        if spec.status == "sealed":
            if not confirm or lock_path is None:
                raise CvprTierError("sealed set requires --confirm --lock")
            lock = load_lock(lock_path)
            verify_lock_for_run(
                lock,
                spec,
                holistic_mode=holistic_mode,
                holistic_samples=holistic_samples,
                member_ids=member_ids,
            )
        elif confirm or lock_path is not None:
            raise CvprTierError("--confirm --lock is only for a sealed set")
    except SetError as exc:
        raise CvprTierError(str(exc)) from exc


def _usage_row(handoff_id: str, prompt: str, outcome: dict[str, Any]) -> dict[str, Any]:
    return {
        "estimated_tokens": outcome.get("estimated_tokens", estimated_tokens(len(prompt))),
        "handoff_id": handoff_id,
        "latency_seconds": outcome.get("latency_seconds"),
        "parsed": bool(outcome.get("parsed")),
        "prompt_chars": len(prompt),
        "retries": int(outcome.get("retries") or 0),
        "usage": outcome.get("usage"),
    }


def _stored_answer_text(answers: Path, handoff_id: str) -> str | None:
    """Return a non-empty answer already stored under the opaque handoff id."""
    path = answers / f"{handoff_id}.json"
    if path.is_file() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8")
    return None


def _outcome_from_stored_answer(
    text: str,
    mode: str,
    samples: int,
    *,
    transport: str,
) -> dict[str, Any]:
    """Reuse a finished answer. Does not call the model again."""
    try:
        verdict = parse_verdict(mode, text, samples=samples)
    except CvprTierError as exc:
        return {
            "error": str(exc),
            "latency_seconds": None,
            "parsed": False,
            "prompt_transport": transport,
            "retries": 0,
            "text": text,
            "usage": None,
        }
    return {
        "latency_seconds": None,
        "parsed": True,
        "prompt_transport": transport,
        "retries": 0,
        "text": text,
        "usage": None,
        "verdict": verdict,
    }


def _command_paper(
    *,
    template: str,
    prompt: str,
    handoff_id: str,
    model_id: str,
    mode: str,
    samples: int,
    timeout: float,
    failure_log: Path,
) -> dict[str, Any]:
    """One local CLI call, retried once after a timeout or a parse failure."""
    def parse_answer(payload: Any) -> dict[str, Any]:
        body, usage, sentinel = split_usage(json.dumps(payload, ensure_ascii=False))
        verdict = _parse_verdict_payload(mode, json.loads(body), samples=samples)
        return {"text": body, "usage": usage, "sentinel": sentinel, "verdict": verdict}

    observed: dict[str, Any] = {
        "argv0": "",
        "cli_flags": [],
        "cli_version": "",
        "latency_seconds": None,
        "prompt_transport": "stdin",
        "sentinel": None,
        "usage": None,
    }
    for attempt in (1, 2):
        try:
            result = run_isolated_command(
                template,
                prompt=prompt,
                handoff_id=handoff_id,
                model_id=model_id,
                mode=mode,
                timeout=timeout,
                render=render_command,
            )
        except HandoffError as exc:
            message = str(exc)
            kind = "timeout" if "timed out" in message else "command"
            _append_jsonl(
                failure_log,
                {"attempt": attempt, "error": message, "handoff_id": handoff_id, "kind": kind},
            )
            if attempt == 2:
                raise CvprTierError(message) from exc
            continue
        observed.update(
            {
                "argv0": result["argv0"],
                "cli_flags": result["cli_flags"],
                "cli_version": result["cli_version"],
                "estimated_tokens": result["estimated_tokens"],
                "latency_seconds": result["latency_seconds"],
                "prompt_transport": result["prompt_transport"],
                "sentinel": result["sentinel"],
                "usage": result["usage"],
            }
        )
        try:
            answer = parse_json_output(result["raw"], parse_answer)
        except JsonOutputError as exc:
            message = f"model output is {exc}"
            _append_jsonl(
                failure_log,
                {"attempt": attempt, "error": message, "handoff_id": handoff_id, "kind": "parse"},
            )
            if attempt == 2:
                return {**observed, "error": message, "parsed": False, "retries": 1, "text": result["text"]}
            continue
        return {**observed, **answer, "parsed": True, "retries": attempt - 1}
    raise CvprTierError(f"command failed for {handoff_id}")


def _collapse_repeats(series: list[dict[str, Any]]) -> dict[str, Any]:
    """One outcome per paper. The stored score is the mean of parsed repeats."""
    if len(series) == 1:
        outcome = dict(series[0])
        verdict = outcome.get("verdict")
        if isinstance(verdict, dict):
            outcome["repeat_scores"] = [verdict["score"]]
            outcome["repeat_tiers"] = [verdict["predicted_tier"]]
        return outcome
    parsed = [item for item in series if item.get("parsed") and isinstance(item.get("verdict"), dict)]
    retries = sum(int(item.get("retries") or 0) for item in series)
    tokens = sum(int(item.get("estimated_tokens") or 0) for item in series)
    if not parsed:
        failed = dict(series[-1])
        failed["estimated_tokens"] = tokens
        failed["parsed"] = False
        failed["retries"] = retries
        return failed
    scores = [float(item["verdict"]["score"]) for item in parsed]
    tiers = [str(item["verdict"]["predicted_tier"]) for item in parsed]
    verdict = dict(parsed[0]["verdict"])
    verdict["score"] = sum(scores) / len(scores)
    verdict["predicted_tier"] = Counter(tiers).most_common(1)[0][0]
    outcome = dict(parsed[-1])
    outcome["estimated_tokens"] = tokens
    outcome["parsed"] = True
    outcome["repeat_scores"] = scores
    outcome["repeat_tiers"] = tiers
    outcome["retries"] = retries
    outcome["verdict"] = verdict
    return outcome


def run_model(
    cache: Path,
    *,
    model_id: str,
    backend: str,
    holistic_mode: str = "forecast",
    holistic_samples: int = 1,
    command_template: str | None = None,
    command_timeout: float = DEFAULT_MODEL_TIMEOUT_SECONDS,
    adapter: str | None = None,
    base_url: str | None = None,
    endpoint_model: str | None = None,
    api_key_env: str = "APR_MODEL_API_KEY",
    allow_remote: bool = False,
    network_policy: str = "deny",
    temperature: float | None = None,
    completion: Any = None,
    set_id: str | None = None,
    variant: str | None = None,
    confirm: bool = False,
    lock_path: Path | None = None,
    handoff_dir: Path | None = None,
    custody: Path | None = None,
    repeats: int = 1,
    round_id: str | None = None,
) -> dict[str, Any]:
    """Judge the sample. Does not open label files, meta, or the custody directory."""
    if backend == "adapter" or completion is not None and backend != "command":
        raise CvprTierError(
            "Models run through the local CLI command backend. "
            "The adapter backend is not used and no API key is read."
        )
    if backend not in {"command", "replay"}:
        raise CvprTierError("backend must be command or replay")
    if repeats < 1 or repeats > 10:
        raise CvprTierError("repeats must be from 1 to 10")
    if repeats != 1 and backend != "command":
        raise CvprTierError("repeats require the command backend")
    if holistic_mode not in HOLISTIC_MODES:
        raise CvprTierError(f"holistic_mode must be one of {', '.join(HOLISTIC_MODES)}")
    if variant is not None and variant not in {"A0", "A1", "A2"}:
        raise CvprTierError("variant must be A0, A1, or A2")
    label = parse_model_id(model_id)
    _reject_remote_grok(label, backend, base_url)
    if backend == "command":
        render_command(
            command_template or "",
            {
                "prompt_file": "prompt.txt",
                "answer_file": "answer.json",
                "model": label,
                "paper_id": "paper",
                "handoff_id": "paper",
                "mode": holistic_mode,
            },
        )
    root = _cache(cache)
    _remember_custody(root, custody)
    manifest = _load_sample(root, set_id)
    _enforce_sealed_run(
        root,
        set_id,
        confirm=confirm,
        lock_path=None if lock_path is None else Path(lock_path),
        holistic_mode=holistic_mode,
        holistic_samples=holistic_samples,
        member_ids=[paper.paper_id for paper in manifest.papers],
    )
    material = _material_root(root, set_id)
    leak_failures = _run_leak_failures(root, manifest, variant=variant, set_id=set_id)
    eligible = [paper for paper in manifest.papers if paper.paper_id not in leak_failures]
    directory = run_directory(material, label, variant=variant, round_id=round_id)
    answers = directory / "answers"
    parsed = directory / "parsed"
    answers.mkdir(parents=True, exist_ok=True)
    parsed.mkdir(parents=True, exist_ok=True)
    salt = load_or_create_salt(root)
    handoff_ids: dict[str, str] = {}
    seen: set[str] = set()
    for paper in manifest.papers:
        handoff_id = handoff_id_for(salt, paper.paper_id)
        if handoff_id in seen:
            raise CvprTierError("handoff id collision")
        seen.add(handoff_id)
        handoff_ids[paper.paper_id] = handoff_id
    skipped: list[dict[str, Any]] = []
    for paper_id, counts in leak_failures.items():
        record = {
            "status": "skipped",
            "reason": "leak_failed",
            "paper_id": paper_id,
            "variant": variant,
            "handoff_id": handoff_ids[paper_id],
            "model_id": label,
            "holistic_mode": holistic_mode,
            "leak_counts": counts,
        }
        _write_json(parsed / f"{handoff_ids[paper_id]}.json", record)
        skipped.append(record)
    prompts: dict[str, str] = {}
    packet_sha: dict[str, str] = {}
    for paper in eligible:
        prompt, digest = _prompt_for(
            root,
            paper.paper_id,
            holistic_mode,
            holistic_samples,
            variant=variant,
            set_id=set_id,
            model_id=label,
        )
        prompts[paper.paper_id] = prompt
        packet_sha[paper.paper_id] = digest
    handoff_root: Path | None = None
    exports: list[dict[str, str]] = []
    if backend == "replay":
        try:
            assert_model_visible([label], what="replay directory")
            handoff_root = _handoff_root(root, None if handoff_dir is None else Path(handoff_dir)) / label
        except HandoffError as exc:
            raise CvprTierError(str(exc)) from exc
        handoff_root.mkdir(parents=True, exist_ok=True)
        for paper in eligible:
            handoff_id = handoff_ids[paper.paper_id]
            try:
                exported = export_replay_packet(
                    handoff_root,
                    handoff_id=handoff_id,
                    prompt=prompts[paper.paper_id],
                    packet_sha256=packet_sha[paper.paper_id],
                    holistic_mode=holistic_mode,
                    holistic_samples=holistic_samples,
                )
            except HandoffError as exc:
                raise CvprTierError(str(exc)) from exc
            exports.append(
                {
                    "directory": str(exported),
                    "handoff_id": handoff_id,
                    "paper_id": paper.paper_id,
                }
            )
    params = {
        "command_template": command_template,
        "adapter": adapter,
        "base_url": base_url,
        "endpoint_model": endpoint_model,
        "api_key_env": api_key_env if backend == "adapter" else None,
        "allow_remote": bool(allow_remote),
        "network_policy": network_policy,
        "temperature": temperature if backend == "adapter" else None,
        "command_timeout": command_timeout if backend == "command" else None,
    }
    failure_log = directory / "failures.jsonl"
    outcomes: dict[str, dict[str, Any]] = {}
    cli_version = ""
    cli_flags: list[str] = []
    prompt_transport = "replay" if backend == "replay" else backend
    if backend == "command":
        if completion is not None:
            raise CvprTierError("command backend does not take a completion object")
        command_transport = (
            "prompt_file" if command_template and "{prompt_file}" in command_template else "stdin"
        )
        for paper in eligible:
            handoff_id = handoff_ids[paper.paper_id]
            stored = _stored_answer_text(answers, handoff_id)
            if stored is not None:
                outcome = _outcome_from_stored_answer(
                    stored,
                    holistic_mode,
                    holistic_samples,
                    transport=command_transport,
                )
            else:
                series: list[dict[str, Any]] = []
                for _repeat_index in range(repeats):
                    series.append(
                        _command_paper(
                            template=command_template or "",
                            prompt=prompts[paper.paper_id],
                            handoff_id=handoff_id,
                            model_id=label,
                            mode=holistic_mode,
                            samples=holistic_samples,
                            timeout=command_timeout,
                            failure_log=failure_log,
                        )
                    )
                outcome = _collapse_repeats(series)
            outcomes[paper.paper_id] = outcome
            cli_version = str(outcome.get("cli_version") or cli_version)
            cli_flags = list(outcome.get("cli_flags") or cli_flags)
            prompt_transport = str(outcome.get("prompt_transport") or prompt_transport)
    elif backend == "adapter":
        raise CvprTierError(
            "Models run through the local CLI command backend. "
            "The adapter backend is not used and no API key is read."
        )
    else:
        canaries = load_canaries(root)
        state_path = directory / "parse_state.json"
        state: dict[str, Any] = _read_json(state_path) if state_path.is_file() else {}
        export_by_paper = {item["paper_id"]: item for item in exports}
        for paper in eligible:
            handoff_id = handoff_ids[paper.paper_id]
            exported = Path(export_by_paper[paper.paper_id]["directory"])
            if not (exported / "answer.json").is_file() and not (exported / "answer.txt").is_file():
                outcomes[paper.paper_id] = {
                    "awaiting": True,
                    "latency_seconds": None,
                    "parsed": False,
                    "retries": 0,
                    "usage": None,
                }
                continue
            record = state.get(handoff_id) or {"attempts": 0}
            if int(record.get("attempts") or 0) >= 2:
                outcomes[paper.paper_id] = {
                    "error": "parse retry already used",
                    "latency_seconds": None,
                    "parsed": False,
                    "retries": 1,
                    "usage": None,
                }
                continue
            try:
                text = import_replay_answer(
                    exported,
                    handoff_id=handoff_id,
                    packet_sha256=packet_sha[paper.paper_id],
                    canaries=canaries,
                )
            except HandoffError as exc:
                outcomes[paper.paper_id] = {
                    "error": str(exc),
                    "latency_seconds": None,
                    "parsed": False,
                    "retries": 0,
                    "usage": None,
                }
                continue
            record["attempts"] = int(record.get("attempts") or 0) + 1
            state[handoff_id] = record
            try:
                verdict = parse_verdict(holistic_mode, text, samples=holistic_samples)
            except CvprTierError as exc:
                _append_jsonl(
                    failure_log,
                    {
                        "attempt": record["attempts"],
                        "error": str(exc),
                        "handoff_id": handoff_id,
                        "kind": "parse",
                    },
                )
                outcomes[paper.paper_id] = {
                    "error": str(exc),
                    "latency_seconds": None,
                    "parsed": False,
                    "retries": max(0, int(record["attempts"]) - 1),
                    "text": text,
                    "usage": None,
                }
                continue
            outcomes[paper.paper_id] = {
                "latency_seconds": None,
                "parsed": True,
                "retries": max(0, int(record["attempts"]) - 1),
                "text": text,
                "usage": None,
                "verdict": verdict,
            }
        _write_json(state_path, state)
    awaiting: list[str] = []
    errors: list[dict[str, str]] = []
    done: list[str] = []
    usage_rows: list[dict[str, Any]] = [
        {
            **record,
            **_usage_row(record["handoff_id"], "", {"estimated_tokens": 0, "latency_seconds": 0}),
        }
        for record in skipped
    ]
    for paper in eligible:
        handoff_id = handoff_ids[paper.paper_id]
        outcome = outcomes[paper.paper_id]
        usage_rows.append(_usage_row(handoff_id, prompts[paper.paper_id], outcome))
        if outcome.get("sentinel") is not None:
            _write_json(directory / "sentinel" / f"{handoff_id}.json", outcome["sentinel"])
        if outcome.get("text"):
            (answers / f"{handoff_id}.json").write_text(outcome["text"], encoding="utf-8")
        if outcome.get("awaiting"):
            awaiting.append(handoff_id)
            continue
        if not outcome.get("parsed"):
            errors.append(
                {
                    "error": str(outcome.get("error") or "parse failed"),
                    "handoff_id": handoff_id,
                    "paper_id": paper.paper_id,
                }
            )
            continue
        verdict = outcome["verdict"]
        _write_json(
            parsed / f"{handoff_id}.json",
            {
                "handoff_id": handoff_id,
                "holistic_mode": holistic_mode,
                "model_id": label,
                "packet_sha256": packet_sha[paper.paper_id],
                "paper_id": paper.paper_id,
                "predicted_tier": verdict["predicted_tier"],
                "rank_samples_used": verdict["rank_samples_used"],
                "rank_source": verdict["rank_source"],
                "repeat_scores": outcome.get("repeat_scores") or [verdict["score"]],
                "repeat_tiers": outcome.get("repeat_tiers") or [verdict["predicted_tier"]],
                "sample_ranks": verdict["sample_ranks"],
                "score": verdict["score"],
            },
        )
        done.append(paper.paper_id)
    _write_json(
        directory / "usage.json",
        {
            "backend": backend,
            "cli_flags": cli_flags,
            "cli_version": cli_version,
            "model_id": label,
            "model_version": cli_version or None,
            "papers": usage_rows,
            "prompt_transport": prompt_transport,
            "repeats": repeats,
            "retry_max": 1,
            "round": round_id,
            "set_id": set_id,
            "skipped": len(skipped),
            "variant": variant,
        },
    )
    _write_json(
        directory / "manifest.json",
        {
            "backend": backend,
            "cli_flags": cli_flags,
            "cli_version": cli_version,
            "exports": exports,
            "handoff_dir": None if handoff_root is None else str(handoff_root),
            "handoff_ids": handoff_ids,
            "holistic_mode": holistic_mode,
            "holistic_samples": holistic_samples,
            "model_id": label,
            "model_version": cli_version or None,
            "packet_sha256": packet_sha,
            "papers": [paper.paper_id for paper in manifest.papers],
            "params": params,
            "prompt_transport": prompt_transport,
            "repeats": repeats,
            "round": round_id,
            "sample_seed": manifest.seed,
            "set_id": set_id,
            "skipped": [record["paper_id"] for record in skipped],
            "variant": variant,
        },
    )
    if round_id:
        from .cvpr_rounds import append_round

        append_round(
            root,
            {
                "backend": backend,
                "calls": len(eligible) * repeats,
                "model_id": label,
                "parsed": len(done),
                "repeats": repeats,
                "round": round_id,
                "set_id": set_id,
                "skipped": len(skipped),
                "tokens": sum(int(row.get("estimated_tokens") or 0) for row in usage_rows),
                "variant": variant,
            },
        )
    return {
        "awaiting_judgment": awaiting,
        "backend": backend,
        "errors": errors,
        "handoff_dir": None if handoff_root is None else str(handoff_root),
        "model_id": label,
        "parsed": done,
        "prompt_transport": prompt_transport,
        "skipped": skipped,
    }


def _interval(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    ordered = sorted(values)
    return _linear_percentile(ordered, 0.025), _linear_percentile(ordered, 0.975)


def _rows_for_model(
    parsed_rows: list[dict[str, Any]],
    labels: dict[str, str],
    *,
    years: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    years = years or {}
    rows = []
    for row in parsed_rows:
        if row.get("status") == "skipped":
            continue
        tier = labels.get(str(row["paper_id"]))
        if tier not in TIER_RANK:
            continue
        item: dict[str, Any] = {
            "paper_id": row["paper_id"],
            "score": float(row["score"]),
            "predicted_tier": str(row["predicted_tier"]),
            "true_tier": tier,
            "year": years.get(str(row["paper_id"])),
        }
        if isinstance(row.get("repeat_scores"), list):
            item["repeat_scores"] = [float(score) for score in row["repeat_scores"]]
        rows.append(item)
    return rows


def _spearman_of(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 2:
        return None
    return spearman([row["score"] for row in rows], [float(TIER_RANK[row["true_tier"]]) for row in rows])


def _auc_of(rows: list[dict[str, Any]], high: str, low: str) -> float | None:
    scores: list[float] = []
    labels: list[int] = []
    for row in rows:
        if row["true_tier"] == high:
            scores.append(row["score"])
            labels.append(1)
        elif row["true_tier"] == low:
            scores.append(row["score"])
            labels.append(0)
    if not scores:
        return None
    return roc_auc(scores, labels)


def _confusion(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    matrix = {truth: {predicted: 0 for predicted in PREDICTED_TIERS} for truth in ACCEPTED_TIERS}
    for row in rows:
        truth = row["true_tier"]
        predicted = row["predicted_tier"]
        if truth in matrix and predicted in matrix[truth]:
            matrix[truth][predicted] += 1
    return matrix


def _spread(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [row["score"] for row in rows]
    stdev = statistics.stdev(scores) if len(scores) >= 2 else None
    coarse = {round(score, 1) for score in scores}
    clustered = bool(scores) and (
        len(coarse) < SPREAD_UNIQUE_FLOOR or stdev is None or stdev < SPREAD_STDEV_FLOOR
    )
    return {
        "stdev": None if stdev is None else round(stdev, 4),
        "minimum": None if not scores else min(scores),
        "maximum": None if not scores else max(scores),
        "unique_rounded": len(coarse),
        "clustered": clustered,
    }


def _bootstrap(rows: list[dict[str, Any]], statistic, *, resamples: int, seed: int) -> dict[str, Any]:
    point = statistic(rows)
    rng = random.Random(seed)
    drawn: list[float] = []
    count = len(rows)
    if count == 0:
        return {
            "point": None,
            "low": None,
            "high": None,
            "defined_resamples": 0,
            "resamples": resamples,
            "seed": seed,
        }
    for _ in range(resamples):
        sample = [rows[rng.randrange(count)] for _ in range(count)]
        value = statistic(sample)
        if value is None:
            continue
        drawn.append(float(value))
    low, high = _interval(drawn)
    return {
        "point": point,
        "low": None if low is None else round(low, 4),
        "high": None if high is None else round(high, 4),
        "defined_resamples": len(drawn),
        "resamples": resamples,
        "seed": seed,
    }


def model_metrics(rows: list[dict[str, Any]], *, resamples: int, seed: int) -> dict[str, Any]:
    """Spearman, pairwise AUCs, confusion, and spread. No coefficient is fit."""
    metrics = {
        "n": len(rows),
        "spearman": _bootstrap(rows, _spearman_of, resamples=resamples, seed=seed),
        "pairwise_auc": {},
        "confusion": _confusion(rows),
        "spread": _spread(rows),
    }
    for name, high, low in PAIRWISE:
        metrics["pairwise_auc"][name] = _bootstrap(
            rows,
            lambda sample, high=high, low=low: _auc_of(sample, high, low),
            resamples=resamples,
            seed=seed,
        )
    return metrics


def _pair_rows(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    by_right = {row["paper_id"]: row for row in right}
    return [(row, by_right[row["paper_id"]]) for row in left if row["paper_id"] in by_right]


def _pair_spearman(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> float | None:
    if len(pairs) < 2:
        return None
    return spearman([left["score"] for left, _ in pairs], [right["score"] for _, right in pairs])


def _pair_agreement(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> float | None:
    if not pairs:
        return None
    hits = sum(left["predicted_tier"] == right["predicted_tier"] for left, right in pairs)
    return hits / len(pairs)


def compare_models(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    pairs = _pair_rows(left, right)

    def spearman_stat(sample: list[tuple[dict[str, Any], dict[str, Any]]]) -> float | None:
        return _pair_spearman(sample)

    def agree_stat(sample: list[tuple[dict[str, Any], dict[str, Any]]]) -> float | None:
        return _pair_agreement(sample)

    return {
        "n": len(pairs),
        "score_spearman": _bootstrap(pairs, spearman_stat, resamples=resamples, seed=seed),
        "tier_agreement": _bootstrap(pairs, agree_stat, resamples=resamples, seed=seed),
    }


def _format_interval(interval: dict[str, Any]) -> str:
    point = interval.get("point")
    low = interval.get("low")
    high = interval.get("high")
    if not isinstance(point, (int, float)):
        return "n/a"
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return f"{point:.3f}"
    return f"{float(point):.3f} [{float(low):.3f}, {float(high):.3f}]"


def _run_key(manifest: dict[str, Any], directory: Path) -> str:
    model = str(manifest.get("model_id") or directory.name)
    parts = [model]
    if manifest.get("variant"):
        parts.append(str(manifest["variant"]))
    if manifest.get("round"):
        parts.append(str(manifest["round"]))
    return "/".join(parts)


def _load_parsed(cache: Path) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    runs = cache / "runs"
    found: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    if not runs.is_dir():
        return found
    for manifest_path in sorted(runs.rglob("manifest.json")):
        directory = manifest_path.parent
        parsed_dir = directory / "parsed"
        if not parsed_dir.is_dir():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = []
        for path in sorted(parsed_dir.glob("*.json")):
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        key = _run_key(manifest, directory)
        if key in found:
            raise CvprTierError(f"duplicate run {key}")
        found[key] = (manifest, rows)
    return found


def _report_labels(
    root: Path,
    set_id: str | None,
    *,
    confirm: bool,
    lock_path: Path | None,
    custody: Path | None,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Operator-side labels. A sealed set is read once, from the custody directory."""
    material = _material_root(root, set_id)
    if not set_id:
        if confirm or lock_path is not None or custody is not None:
            raise CvprTierError("--confirm --lock --custody requires --set")
        labels_path = root / "heldout" / "tiers.json"
        if not labels_path.is_file():
            raise CvprTierError("heldout/tiers.json is missing; run fetch first")
        labels = HeldoutTiers.model_validate(_read_json(labels_path)).papers
        return labels, None
    from .cvpr_sets import (
        SetError,
        assert_not_opened,
        ensure_cache_registry,
        find_set,
        load_lock,
        verify_label_hash,
        verify_lock_for_run,
    )

    try:
        spec = find_set(ensure_cache_registry(root), set_id)
        if spec.status == "opened":
            if confirm or lock_path is not None or custody is not None:
                raise CvprTierError("report --confirm already opened this set")
        elif spec.status == "sealed":
            if not confirm or lock_path is None or custody is None:
                raise CvprTierError("sealed set report requires --confirm --lock --custody")
            assert_not_opened(custody, set_id)
            lock = load_lock(lock_path)
            sample = _load_sample(root, set_id)
            verify_lock_for_run(
                lock,
                spec,
                holistic_mode=str((lock.get("task") or {}).get("holistic_mode") or ""),
                holistic_samples=int((lock.get("task") or {}).get("k") or 0),
                member_ids=[paper.paper_id for paper in sample.papers],
            )
            labels_path = custody / set_id / "tiers.json"
            if not labels_path.is_file():
                raise CvprTierError("custody tiers.json is missing")
            verify_label_hash(lock, spec, labels_path)
            labels = HeldoutTiers.model_validate(_read_json(labels_path)).papers
            return labels, {"custody": custody, "lock": lock}
        if confirm or lock_path is not None or custody is not None:
            raise CvprTierError("--confirm --lock --custody is only for a sealed set")
    except SetError as exc:
        raise CvprTierError(str(exc)) from exc
    labels_path = material / "heldout" / "tiers.json"
    if not labels_path.is_file():
        raise CvprTierError("heldout/tiers.json is missing; run fetch first")
    labels = HeldoutTiers.model_validate(_read_json(labels_path)).papers
    return labels, None


def _mark_report_opened(root: Path, set_id: str, seal: dict[str, Any], report_path: Path) -> None:
    from .cvpr_sets import (
        SetError,
        ensure_cache_registry,
        find_set,
        mark_opened,
        save_cache_registry,
        transition,
    )
    from .hashing import sha256_file

    try:
        mark_opened(
            seal["custody"],
            set_id,
            {
                "lock_sha256": seal["lock"].get("sha256"),
                "report_sha256": sha256_file(report_path),
                "set_id": set_id,
            },
        )
        registry = ensure_cache_registry(root)
        spec = find_set(registry, set_id)
        if spec.status == "sealed":
            transition(spec, "opened")
            save_cache_registry(root, registry)
    except SetError as exc:
        raise CvprTierError(str(exc)) from exc


def _probe_flags(probe_summary: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    model_id = manifest.get("model_id")
    variant = manifest.get("variant")
    for summary in probe_summary.get("models", {}).values():
        if summary.get("model_id") == model_id and summary.get("variant") == variant:
            papers = summary.get("papers")
            if isinstance(papers, dict):
                return papers
    return {}


def _variant_pairs(
    runs: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]],
    joined: dict[str, list[dict[str, Any]]],
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Pair variants of one model and round on the papers both scored."""
    grouped: dict[tuple[Any, Any], list[str]] = defaultdict(list)
    for key, (manifest, _rows) in runs.items():
        grouped[(manifest.get("model_id"), manifest.get("round"))].append(key)
    pairs: dict[str, Any] = {}
    for keys in grouped.values():
        if len(keys) < 2:
            continue
        for index, left_name in enumerate(keys):
            for right_name in keys[index + 1 :]:
                left_variant = runs[left_name][0].get("variant")
                right_variant = runs[right_name][0].get("variant")
                if left_variant == right_variant:
                    continue
                key = f"{left_name}__{right_name}"
                pairs[key] = {
                    "left": left_name,
                    "left_variant": left_variant,
                    "right": right_name,
                    "right_variant": right_variant,
                    **compare_models(joined[left_name], joined[right_name], resamples=resamples, seed=seed),
                }
    return pairs


def _rounds_with_metrics(rows: list[dict[str, Any]], per_model: dict[str, Any]) -> list[dict[str, Any]]:
    """Show macro-AUC from the matching run. rounds.jsonl itself is not rewritten."""
    display: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        if not isinstance(copy.get("macro_auc"), (int, float)):
            for body in per_model.values():
                manifest = body["manifest"]
                if manifest.get("model_id") != copy.get("model_id"):
                    continue
                if manifest.get("round") != copy.get("round"):
                    continue
                if copy.get("variant") not in (None, "", manifest.get("variant")):
                    continue
                point = body["metrics"].get("extended", {}).get("macro_auc", {}).get("point")
                if isinstance(point, (int, float)):
                    copy["macro_auc"] = point
                break
        display.append(copy)
    return display


def _format_point(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):.3f}"


def _format_extended(
    per_model: dict[str, Any],
    comparisons: dict[str, Any],
    variant_pairs: dict[str, Any],
    probe_summary: dict[str, Any],
) -> list[str]:
    lines = [
        "## Primary metric",
        "",
        "macro-AUC is the mean of oral-versus-poster, highlight-versus-poster, and oral-versus-highlight.",
        "Cpop uses the 2024 weights 0.211, 0.760, and 0.030 on those three AUCs.",
        "AUC oral-versus-rest treats oral as the positive class. The 2024 weighted form is (324 * AUC oral-versus-highlight + 2302 * AUC oral-versus-poster) / 2626.",
        "The 2025 oral-versus-rest contrast is reported when that year is in the sample. It is undefined without a non-oral row.",
        "Intervals on macro-AUC and quadratic kappa resample inside each year and true tier.",
        "",
        "| model | macro-AUC | Cpop | oral vs rest | 2024 weighted oral vs rest | 2025 oral vs rest | kappa | ICC(2,1) | ICC(3,1) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model_id, body in per_model.items():
        extended = body["metrics"]["extended"]
        icc = extended["icc"]
        lines.append(
            "| {model} | {macro} | {cpop} | {rest} | {weighted} | {year_2025} | {kappa} | {icc21} | {icc31} |".format(
                model=model_id,
                macro=_format_interval(extended["macro_auc"]),
                cpop=_format_point(extended["cpop"]),
                rest=_format_point(extended["auc_o_rest"]),
                weighted=_format_point(extended["auc_o_rest_weighted_2024"]),
                year_2025=_format_point(extended["auc_oral_vs_rest_2025"]),
                kappa=_format_interval(extended["quadratic_kappa"]),
                icc21=_format_point(icc.get("icc_2_1")),
                icc31=_format_point(icc.get("icc_3_1")),
            )
        )
    lines.extend(
        [
            "",
            "## Horvitz–Thompson weights",
            "",
            "Each paper is weighted by the public population of its year and tier, divided by the number of sampled papers in that cell. 2024 populations are oral 90, highlight 324, poster 2302. 2025 oral is 95 and non-oral is 2776.",
            "",
            "| model | spearman | kendall | labelled share | HT labelled share |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for model_id, body in per_model.items():
        extended = body["metrics"]["extended"]
        share = extended["labelled_share"]
        lines.append(
            "| {model} | {spearman} | {kendall} | {share} | {ht} |".format(
                model=model_id,
                spearman=_format_point(extended["spearman_ht"]),
                kendall=_format_point(extended["kendall_ht"]),
                share=_format_point(share.get("predictions")),
                ht=_format_point(share.get("ht_weighted_predictions")),
            )
        )
        precision = extended["precision_recall_ht"]
        lines.extend(
            [
                "",
                f"### Per-tier precision and recall {model_id}",
                "",
                "HT-weighted. Support is the weighted count of that true tier.",
                "",
                "| tier | precision | recall | support |",
                "| --- | --- | --- | --- |",
            ]
        )
        for tier in PREDICTED_TIERS:
            cell = precision[tier]
            lines.append(
                f"| {tier} | {_format_point(cell['precision'])} | {_format_point(cell['recall'])} | {_format_point(cell['support'])} |"
            )
    lines.extend(
        [
            "",
            "## DeLong",
            "",
            "Single-model p-values test AUC against 0.5. Holm and Benjamini–Hochberg use the three pairwise contrasts as one family. Oral-versus-rest is reported and is not in that family.",
            "",
            "| model | contrast | auc | se | p | holm | bh |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for model_id, body in per_model.items():
        delong = body["metrics"]["extended"]["delong"]
        for name in ("oral_vs_poster", "highlight_vs_poster", "oral_vs_highlight", "oral_vs_rest"):
            estimate = delong[name]
            lines.append(
                "| {model} | {name} | {auc} | {se} | {p} | {holm} | {bh} |".format(
                    model=model_id,
                    name=name,
                    auc=_format_point(estimate.get("auc")),
                    se=_format_point(estimate.get("se")),
                    p=_format_point(estimate.get("p")),
                    holm=_format_point(delong.get("holm", {}).get(name)),
                    bh=_format_point(delong.get("bh", {}).get(name)),
                )
            )
    lines.extend(
        [
            "",
            "## Paired tests",
            "",
            "Paired DeLong uses the papers both runs scored. The macro-AUC difference interval resamples those papers inside each year and true tier.",
            "",
            "| left | right | contrast | diff | p | macro-AUC diff |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if not comparisons:
        lines.append("| n/a | n/a | n/a | n/a | n/a | n/a |")
    for body in comparisons.values():
        paired = body.get("paired") or {}
        delong = paired.get("delong") or {}
        for name in ("oral_vs_poster", "highlight_vs_poster", "oral_vs_highlight"):
            estimate = delong.get(name) or {}
            lines.append(
                "| {left} | {right} | {name} | {diff} | {p} | {macro} |".format(
                    left=body["left"],
                    right=body["right"],
                    name=name,
                    diff=_format_point(estimate.get("diff")),
                    p=_format_point(estimate.get("p")),
                    macro=_format_interval(paired.get("macro_auc_diff") or {}),
                )
            )
    lines.extend(
        [
            "",
            "## Variant pairs",
            "",
            "Variants of the same model and round, on the papers both variants scored.",
            "",
            "| left | right | n | score spearman | tier agreement |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if not variant_pairs:
        lines.append("| n/a | n/a | 0 | n/a | n/a |")
    for body in variant_pairs.values():
        lines.append(
            "| {left} | {right} | {n} | {spearman} | {agree} |".format(
                left=body["left"],
                right=body["right"],
                n=body["n"],
                spearman=_format_interval(body["score_spearman"]),
                agree=_format_interval(body["tier_agreement"]),
            )
        )
    lines.extend(
        [
            "",
            "## Recognised and unrecognised",
            "",
            "A paper is recognised when the abstract probe hits the title, either the abstract or the title probe hits an author, or the verbatim probe is right on at least 3 of 4 questions. Tiers are joined only in this report.",
            "",
            "| model | recognised n | recognised macro-AUC | unrecognised n | unrecognised macro-AUC |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for model_id, body in per_model.items():
        recognition = body["metrics"]["recognition"]
        lines.append(
            "| {model} | {rn} | {rm} | {un} | {um} |".format(
                model=model_id,
                rn=recognition["recognised"]["n"],
                rm=_format_point(recognition["recognised"]["macro_auc"]),
                un=recognition["unrecognised"]["n"],
                um=_format_point(recognition["unrecognised"]["macro_auc"]),
            )
        )
    kind_rows = []
    for run_key, summary in probe_summary.get("models", {}).items():
        for kind, counts in (summary.get("kinds") or {}).items():
            kind_rows.append((run_key, kind, counts))
    lines.extend(
        [
            "",
            "| probe run | kind | n | recognised | mean familiarity |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if not kind_rows:
        lines.append("| n/a | n/a | 0 | 0 | n/a |")
    for run_key, kind, counts in kind_rows:
        lines.append(
            f"| {run_key} | {kind} | {counts['n']} | {counts['recognised']} | {_format_point(counts['mean_familiarity'])} |"
        )
    lines.append("")
    return lines


def write_report(
    cache: Path,
    output: Path,
    *,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    set_id: str | None = None,
    confirm: bool = False,
    lock_path: Path | None = None,
    custody: Path | None = None,
) -> dict[str, Any]:
    """Join parsed scores to heldout tiers and compare models. Does not fit a weight."""
    root = _cache(cache)
    destination = assert_local_only(output)
    material = _material_root(root, set_id)
    labels, seal = _report_labels(
        root,
        set_id,
        confirm=confirm,
        lock_path=None if lock_path is None else Path(lock_path),
        custody=None if custody is None else Path(custody),
    )
    sample_path = material / "sample_manifest.json"
    sample = SampleManifest.model_validate(_read_json(sample_path)) if sample_path.is_file() else None
    years = {} if sample is None else {paper.paper_id: paper.year for paper in sample.papers}
    from .cvpr_metrics import (
        analysis_rows,
        extended_metrics,
        paired_contrast,
        split_by_recognition,
        write_analysis_csv,
    )
    from .cvpr_probe import load_probe_summary
    from .cvpr_rounds import format_rounds_table, load_rounds

    probe_summary = load_probe_summary(material)
    runs = _load_parsed(material)
    if not runs:
        raise CvprTierError("no parsed model runs are in the cache")
    if seal is not None:
        task = seal["lock"].get("task") or {}
        for manifest, _rows in runs.values():
            if manifest.get("holistic_mode") != task.get("holistic_mode"):
                raise CvprTierError("run holistic mode does not match the lock")
            if int(manifest.get("holistic_samples") or 0) != int(task.get("k") or 0):
                raise CvprTierError("run sample count does not match the lock")
    per_model: dict[str, Any] = {}
    joined: dict[str, list[dict[str, Any]]] = {}
    for model_id, (manifest, rows) in runs.items():
        usable = _rows_for_model(rows, labels, years=years)
        flags = _probe_flags(probe_summary, manifest)
        for row in usable:
            flag = flags.get(str(row["paper_id"]))
            if flag is not None:
                row["recognised"] = bool(flag.get("recognised"))
                row["familiarity"] = flag.get("familiarity")
        joined[model_id] = usable
        metrics = model_metrics(usable, resamples=resamples, seed=seed)
        skipped = [row for row in rows if row.get("status") == "skipped"]
        metrics["skipped"] = len(skipped)
        metrics["skipped_by_reason"] = dict(Counter(str(row.get("reason") or "unknown") for row in skipped))
        metrics["extended"] = extended_metrics(usable, resamples=resamples, seed=seed)
        metrics["recognition"] = split_by_recognition(usable, resamples=resamples, seed=seed)
        per_model[model_id] = {
            "manifest": {
                "model_id": manifest.get("model_id"),
                "backend": manifest.get("backend"),
                "holistic_mode": manifest.get("holistic_mode"),
                "holistic_samples": manifest.get("holistic_samples"),
                "params": manifest.get("params"),
                "variant": manifest.get("variant"),
                "round": manifest.get("round"),
                "repeats": manifest.get("repeats"),
            },
            "metrics": metrics,
        }
    comparisons: dict[str, Any] = {}
    names = list(joined)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            key = f"{left_name}__{right_name}"
            comparisons[key] = {
                "left": left_name,
                "right": right_name,
                **compare_models(joined[left_name], joined[right_name], resamples=resamples, seed=seed),
                "paired": paired_contrast(
                    joined[left_name],
                    joined[right_name],
                    resamples=resamples,
                    seed=seed,
                ),
            }
    variant_pairs = _variant_pairs(runs, joined, resamples=resamples, seed=seed)
    payload = {
        "report_only": True,
        "set_id": set_id,
        "seed": None if sample is None else sample.seed,
        "n_per_tier": None if sample is None else sample.n_per_tier,
        "draws": None if sample is None else sample.draws,
        "bootstrap_resamples": resamples,
        "bootstrap_seed": seed,
        "primary_metric": "macro_auc",
        "models": per_model,
        "comparisons": comparisons,
        "variant_pairs": variant_pairs,
        "probes": probe_summary,
    }
    lines = [
        "# CVPR accepted-tier calibration",
        "",
        "Report only. These numbers are not written back into a score, a cutoff, or a venue profile.",
        "The sample is accepted papers. It cannot measure accept versus reject.",
        "Oral, highlight, and poster labels are program decisions. Area chairs and program chairs assign them, so a tier is not a review score.",
        "",
        f"Set: {set_id}." if set_id else "Set: legacy sample.",
        f"Sample seed: {payload['seed']}. Requested per tier: {payload['n_per_tier']}.",
        f"Bootstrap: {resamples} resamples, seed {seed}.",
        "",
        "## Per model",
        "",
        "Skipped manuscripts are counted separately and excluded from scoring and analysis CSV rows.",
        "",
        "| model | backend | mode | n | skipped (leak_failed) | spearman | oral vs poster | highlight vs poster | oral vs highlight | spread stdev | clustered |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model_id, body in per_model.items():
        metrics = body["metrics"]
        manifest = body["manifest"]
        spread = metrics["spread"]
        lines.append(
            "| {model} | {backend} | {mode} | {n} | {skipped} | {spearman} | {oral} | {highlight} | {talk} | {stdev} | {clustered} |".format(
                model=model_id,
                backend=manifest.get("backend"),
                mode=manifest.get("holistic_mode"),
                n=metrics["n"],
                skipped=metrics["skipped_by_reason"].get("leak_failed", 0),
                spearman=_format_interval(metrics["spearman"]),
                oral=_format_interval(metrics["pairwise_auc"]["oral_vs_poster"]),
                highlight=_format_interval(metrics["pairwise_auc"]["highlight_vs_poster"]),
                talk=_format_interval(metrics["pairwise_auc"]["oral_vs_highlight"]),
                stdev="n/a" if spread["stdev"] is None else f"{spread['stdev']:.3f}",
                clustered="yes" if spread["clustered"] else "no",
            )
        )
    for model_id, body in per_model.items():
        matrix = body["metrics"]["confusion"]
        lines.extend(
            [
                "",
                f"## Confusion {model_id}",
                "",
                "Rows are the program tier. Columns are the tier the model named. A numeric cutoff is not applied.",
                "",
                "| true \\ predicted | reject | poster | highlight | oral |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for truth in ACCEPTED_TIERS:
            counts = matrix[truth]
            lines.append(
                f"| {truth} | {counts['reject']} | {counts['poster']} | {counts['highlight']} | {counts['oral']} |"
            )
    lines.extend(
        [
            "",
            "## Agreement between models",
            "",
            "Pairs use the papers both models scored. Score Spearman is between the two ranks. Tier agreement is the fraction of matching tier labels.",
            "",
            "| left | right | n | score spearman | tier agreement |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if not comparisons:
        lines.append("| n/a | n/a | 0 | n/a | n/a |")
    for body in comparisons.values():
        lines.append(
            "| {left} | {right} | {n} | {spearman} | {agree} |".format(
                left=body["left"],
                right=body["right"],
                n=body["n"],
                spearman=_format_interval(body["score_spearman"]),
                agree=_format_interval(body["tier_agreement"]),
            )
        )
    lines.extend(
        [
            "",
            "## Limits",
            "",
            "Accepted papers only, so the loop cannot measure accept versus reject.",
            "A model may still answer reject. That label is a column of the confusion matrix and is not a true tier in this sample.",
            "CVPR 2024 publishes oral, highlight, and poster on the virtual JSON decision field.",
            "CVPR 2025 publishes oral versus poster on eventtype and does not publish highlight, so non-oral 2025 papers are not sampled as posters.",
            "CVPR 2023's virtual JSON does not split tiers. CVPR 2026's static export is a partial page and is not a sampling frame.",
            "",
        ]
    )
    lines.extend(_format_extended(per_model, comparisons, variant_pairs, probe_summary))
    display_rounds = _rounds_with_metrics(load_rounds(root), per_model)
    lines.extend(format_rounds_table(display_rounds))
    lines.append("")
    csv_path = destination.with_suffix(".analysis.csv")
    exported: list[dict[str, Any]] = []
    for model_id, body in per_model.items():
        manifest = body["manifest"]
        exported.extend(
            analysis_rows(
                str(manifest.get("model_id") or model_id),
                joined[model_id],
                variant=manifest.get("variant"),
                round_id=manifest.get("round"),
            )
        )
    write_analysis_csv(csv_path, exported)
    lines.extend(["", f"Analysis rows: `{csv_path.name}`.", ""])
    text = "\n".join(lines)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    _write_json(destination.with_suffix(".json"), payload)
    if seal is not None and set_id is not None:
        _mark_report_opened(root, set_id, seal, destination)
    return payload
