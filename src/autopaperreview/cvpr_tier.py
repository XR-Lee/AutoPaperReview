"""CVPR accepted-tier calibration. Report only.

The loop asks an existing review mode for its usual JSON plus a presentation
tier (reject < poster < highlight < oral). The score is that mode's rank
(``forecast_mean``, ``panel_mean``, the rebuttal final-rating mean, or
``predicted_mean``). Nothing here fits a cutoff or writes a weight back into
``rate``.

Per-paper tiers are written only to ``heldout/tiers.json``. ``prepare`` and
``run`` refuse to read that file or ``catalog.json``. PDFs, prompts, and
model answers stay in a gitignored cache.

A model id is a free-form label (``grok-4.7-cursor``, ``opus-local``,
``astra-local``). Each label has its own run directory. A local CLI supplies
its own argv template; a Cursor agent uses the pending/answers handoff.
"""

from __future__ import annotations

import html
import json
import random
import re
import shlex
import statistics
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
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
from .hashing import sha256_bytes
from .model_exec import (
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    AuditJudgment,
    ExpertJudgment,
    ForecastJudgment,
    HolisticJudgment,
    MatchedJudgment,
    ModelExecutionError,
    ModelSession,
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
COMMAND_PLACEHOLDERS = ("prompt_file", "answer_file", "model", "paper_id", "mode")

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


class SampleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    n_per_tier: dict[str, int]
    years: list[int]
    draws: dict[str, dict[str, int]]
    sources: list[YearSummary]
    papers: list[SamplePaper]


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


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise CvprTierError("model output has no JSON object")
    try:
        value = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CvprTierError(f"model output is not JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise CvprTierError("model output is not a JSON object")
    return value


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
    payload = _json_object(text)
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


def read_judgment_text(cache: Path, path: Path) -> str:
    relative = _relative(cache, path)
    if relative in LABEL_FILES:
        raise CvprTierError(f"judgment must not read {relative}")
    return path.read_text(encoding="utf-8")


def read_judgment_bytes(cache: Path, path: Path) -> bytes:
    relative = _relative(cache, path)
    if relative in LABEL_FILES:
        raise CvprTierError(f"judgment must not read {relative}")
    return path.read_bytes()


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
    catalog = Catalog(papers=papers, years=summaries)
    _write_json(root / "catalog.json", catalog.model_dump())
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


def fetch_sample(
    cache: Path,
    *,
    n_per_tier: int | dict[str, int] = DEFAULT_N_PER_TIER,
    seed: int = DEFAULT_SEED,
    getter: Getter | None = None,
    fixture_dir: Path | None = None,
) -> SampleManifest:
    """Draw the sample and store PDFs or fixture text. Labels go only to heldout/tiers.json."""
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


def _load_sample(cache: Path) -> SampleManifest:
    path = cache / "sample_manifest.json"
    if not path.is_file():
        raise CvprTierError("sample_manifest.json is missing; run fetch first")
    return SampleManifest.model_validate(json.loads(read_judgment_text(cache, path)))


def prepare_manuscripts(cache: Path, *, extract: Callable[[Path], str] | None = None) -> list[dict[str, Any]]:
    """Extract and strip manuscripts. This does not open heldout/tiers.json or catalog.json."""
    root = _cache(cache)
    manifest = _load_sample(root)
    extractor = extract or extract_pdf_text
    rows: list[dict[str, Any]] = []
    for paper in manifest.papers:
        raw_path = root / "raw" / f"{paper.paper_id}.txt"
        pdf_path = root / "pdfs" / f"{paper.paper_id}.pdf"
        if raw_path.is_file():
            raw = read_judgment_text(root, raw_path)
        elif pdf_path.is_file():
            raw = extractor(pdf_path)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(raw, encoding="utf-8")
        else:
            raise CvprTierError(f"no text or PDF for {paper.paper_id}")
        manuscript = strip_cvpr_banner(raw)
        if not manuscript.strip():
            raise CvprTierError(f"manuscript text is empty for {paper.paper_id}")
        destination = root / "manuscripts" / f"{paper.paper_id}.txt"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(manuscript, encoding="utf-8")
        rows.append({"paper_id": paper.paper_id, "characters": len(manuscript)})
    return rows


def _prompt_for(cache: Path, paper_id: str, mode: str, samples: int) -> tuple[str, str]:
    path = cache / "manuscripts" / f"{paper_id}.txt"
    if not path.is_file():
        raise CvprTierError(f"manuscript is missing for {paper_id}; run prepare first")
    manuscript = read_judgment_text(cache, path)
    prompt = build_prompt(manuscript, mode, samples=samples)
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
        session = ModelSession(
            base_url=self.base_url,
            model=self.model,
            api_key_env=self.api_key_env,
            allow_remote=self.allow_remote,
            network_policy=self.network_policy,
            timeout_seconds=self.timeout_seconds,
            temperature=self.temperature,
            json_object=True,
            scratch=scratch,
            stage_signature="cvpr-tier",
            transport=self.transport,
        )
        try:
            session.authorize()
            text = session._exchange(
                "cvpr-tier",
                [{"role": "user", "content": prompt}],
                attempt=1,
            )
        except (ModelExecutionError, PermissionError) as exc:
            raise CvprTierError(str(exc)) from exc
        if not isinstance(text, str) or "{" not in text:
            raise CvprTierError(text or "model response was empty")
        return text


def load_completion_backend(spec: str) -> Any:
    from .adapters import load_review_adapter

    backend = load_review_adapter(spec)
    if not hasattr(backend, "complete"):
        raise CvprTierError(
            "CVPR tier adapter must provide complete(prompt, scratch) -> str. "
            "A local CLI uses --backend command. A file handoff uses --backend replay."
        )
    return backend


def _run_dir(cache: Path, model_id: str) -> Path:
    return cache / "runs" / model_id


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
) -> dict[str, Any]:
    """Judge the sample. Does not open catalog.json or heldout/tiers.json."""
    if backend not in {"adapter", "command", "replay"}:
        raise CvprTierError("backend must be adapter, command, or replay")
    if holistic_mode not in HOLISTIC_MODES:
        raise CvprTierError(f"holistic_mode must be one of {', '.join(HOLISTIC_MODES)}")
    label = parse_model_id(model_id)
    if backend == "command":
        render_command(
            command_template or "",
            {
                "prompt_file": "prompt.txt",
                "answer_file": "answer.json",
                "model": label,
                "paper_id": "paper",
                "mode": holistic_mode,
            },
        )
    root = _cache(cache)
    manifest = _load_sample(root)
    directory = _run_dir(root, label)
    pending = directory / "pending"
    answers = directory / "answers"
    parsed = directory / "parsed"
    pending.mkdir(parents=True, exist_ok=True)
    answers.mkdir(parents=True, exist_ok=True)
    parsed.mkdir(parents=True, exist_ok=True)
    packet_sha: dict[str, str] = {}
    prompts: dict[str, str] = {}
    for paper in manifest.papers:
        prompt, digest = _prompt_for(root, paper.paper_id, holistic_mode, holistic_samples)
        prompts[paper.paper_id] = prompt
        packet_sha[paper.paper_id] = digest
        prompt_path = pending / f"{paper.paper_id}.prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        _write_json(
            pending / f"{paper.paper_id}.json",
            {
                "paper_id": paper.paper_id,
                "model_id": label,
                "holistic_mode": holistic_mode,
                "holistic_samples": holistic_samples,
                "packet_sha256": digest,
                "prompt_file": f"pending/{paper.paper_id}.prompt.txt",
            },
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
    _write_json(
        directory / "manifest.json",
        {
            "model_id": label,
            "backend": backend,
            "holistic_mode": holistic_mode,
            "holistic_samples": holistic_samples,
            "params": params,
            "sample_seed": manifest.seed,
            "papers": [paper.paper_id for paper in manifest.papers],
            "packet_sha256": packet_sha,
        },
    )
    if backend == "command":
        if completion is not None:
            raise CvprTierError("command backend does not take a completion object")
        _run_commands(
            manifest.papers,
            answers,
            template=command_template or "",
            model_id=label,
            mode=holistic_mode,
            timeout=command_timeout,
        )
    elif backend == "adapter":
        completer = completion
        if completer is None:
            if adapter:
                completer = load_completion_backend(adapter)
            elif base_url and endpoint_model:
                completer = OpenAIChatCompletion(
                    base_url=base_url,
                    model=endpoint_model,
                    api_key_env=api_key_env,
                    allow_remote=allow_remote,
                    network_policy=network_policy,
                    temperature=LOCKED_HOLISTIC_TEMPERATURE if temperature is None else temperature,
                )
            else:
                raise CvprTierError("adapter backend requires --adapter or both --base-url and --model")
        scratch = directory / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        for paper in manifest.papers:
            answer_path = answers / f"{paper.paper_id}.json"
            if answer_path.is_file() and answer_path.stat().st_size > 0:
                continue
            text = completer.complete(prompts[paper.paper_id], scratch / paper.paper_id)
            answer_path.write_text(text, encoding="utf-8")
    awaiting: list[str] = []
    errors: list[dict[str, str]] = []
    done: list[str] = []
    for paper in manifest.papers:
        answer_path = _answer_path(answers, paper.paper_id)
        if answer_path is None:
            awaiting.append(paper.paper_id)
            continue
        try:
            verdict = parse_verdict(
                holistic_mode,
                read_judgment_text(root, answer_path),
                samples=holistic_samples,
            )
        except CvprTierError as exc:
            errors.append({"paper_id": paper.paper_id, "error": str(exc)})
            continue
        _write_json(
            parsed / f"{paper.paper_id}.json",
            {
                "paper_id": paper.paper_id,
                "model_id": label,
                "holistic_mode": holistic_mode,
                "score": verdict["score"],
                "predicted_tier": verdict["predicted_tier"],
                "rank_source": verdict["rank_source"],
                "sample_ranks": verdict["sample_ranks"],
                "rank_samples_used": verdict["rank_samples_used"],
                "packet_sha256": packet_sha[paper.paper_id],
            },
        )
        done.append(paper.paper_id)
    return {
        "model_id": label,
        "backend": backend,
        "awaiting_judgment": awaiting,
        "errors": errors,
        "parsed": done,
    }


def _answer_path(answers: Path, paper_id: str) -> Path | None:
    for suffix in (".json", ".txt"):
        path = answers / f"{paper_id}{suffix}"
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _run_commands(
    papers: list[SamplePaper],
    answers: Path,
    *,
    template: str,
    model_id: str,
    mode: str,
    timeout: float,
) -> None:
    for paper in papers:
        answer_path = answers / f"{paper.paper_id}.json"
        if answer_path.is_file() and answer_path.stat().st_size > 0:
            continue
        prompt_path = answer_path.parent.parent / "pending" / f"{paper.paper_id}.prompt.txt"
        argv = render_command(
            template,
            {
                "prompt_file": str(prompt_path),
                "answer_file": str(answer_path),
                "model": model_id,
                "paper_id": paper.paper_id,
                "mode": mode,
            },
        )
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CvprTierError(f"command timed out for {paper.paper_id}") from exc
        except OSError as exc:
            raise CvprTierError(f"command failed to start: {exc}") from exc
        if answer_path.is_file() and answer_path.stat().st_size > 0:
            continue
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace")[:500]
            raise CvprTierError(f"command exited {completed.returncode} for {paper.paper_id}: {detail}")
        stdout = completed.stdout.decode("utf-8", "replace")
        if not stdout.strip():
            raise CvprTierError(f"command wrote no answer for {paper.paper_id}")
        answer_path.write_text(stdout, encoding="utf-8")


def _interval(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    ordered = sorted(values)
    return _linear_percentile(ordered, 0.025), _linear_percentile(ordered, 0.975)


def _rows_for_model(parsed_rows: list[dict[str, Any]], labels: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for row in parsed_rows:
        tier = labels.get(str(row["paper_id"]))
        if tier not in TIER_RANK:
            continue
        rows.append(
            {
                "paper_id": row["paper_id"],
                "score": float(row["score"]),
                "predicted_tier": str(row["predicted_tier"]),
                "true_tier": tier,
            }
        )
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


def _load_parsed(cache: Path) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    runs = cache / "runs"
    found: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    if not runs.is_dir():
        return found
    for directory in sorted(path for path in runs.iterdir() if path.is_dir()):
        manifest_path = directory / "manifest.json"
        parsed_dir = directory / "parsed"
        if not manifest_path.is_file() or not parsed_dir.is_dir():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = []
        for path in sorted(parsed_dir.glob("*.json")):
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        found[str(manifest.get("model_id") or directory.name)] = (manifest, rows)
    return found


def write_report(
    cache: Path,
    output: Path,
    *,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Join parsed scores to heldout tiers and compare models. Does not fit a weight."""
    root = _cache(cache)
    destination = assert_local_only(output)
    labels_path = root / "heldout" / "tiers.json"
    if not labels_path.is_file():
        raise CvprTierError("heldout/tiers.json is missing; run fetch first")
    labels = HeldoutTiers.model_validate(_read_json(labels_path)).papers
    sample_path = root / "sample_manifest.json"
    sample = SampleManifest.model_validate(_read_json(sample_path)) if sample_path.is_file() else None
    runs = _load_parsed(root)
    if not runs:
        raise CvprTierError("no parsed model runs are in the cache")
    per_model: dict[str, Any] = {}
    joined: dict[str, list[dict[str, Any]]] = {}
    for model_id, (manifest, rows) in runs.items():
        usable = _rows_for_model(rows, labels)
        joined[model_id] = usable
        per_model[model_id] = {
            "manifest": {
                "model_id": manifest.get("model_id"),
                "backend": manifest.get("backend"),
                "holistic_mode": manifest.get("holistic_mode"),
                "holistic_samples": manifest.get("holistic_samples"),
                "params": manifest.get("params"),
            },
            "metrics": model_metrics(usable, resamples=resamples, seed=seed),
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
            }
    payload = {
        "report_only": True,
        "seed": None if sample is None else sample.seed,
        "n_per_tier": None if sample is None else sample.n_per_tier,
        "draws": None if sample is None else sample.draws,
        "bootstrap_resamples": resamples,
        "bootstrap_seed": seed,
        "models": per_model,
        "comparisons": comparisons,
    }
    lines = [
        "# CVPR accepted-tier calibration",
        "",
        "Report only. These numbers are not written back into a score, a cutoff, or a venue profile.",
        "The sample is accepted papers. It cannot measure accept versus reject.",
        "Oral, highlight, and poster labels are program decisions. Area chairs and program chairs assign them, so a tier is not a review score.",
        "",
        f"Sample seed: {payload['seed']}. Requested per tier: {payload['n_per_tier']}.",
        f"Bootstrap: {resamples} resamples, seed {seed}.",
        "",
        "## Per model",
        "",
        "| model | backend | mode | n | spearman | oral vs poster | highlight vs poster | oral vs highlight | spread stdev | clustered |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model_id, body in per_model.items():
        metrics = body["metrics"]
        manifest = body["manifest"]
        spread = metrics["spread"]
        lines.append(
            "| {model} | {backend} | {mode} | {n} | {spearman} | {oral} | {highlight} | {talk} | {stdev} | {clustered} |".format(
                model=model_id,
                backend=manifest.get("backend"),
                mode=manifest.get("holistic_mode"),
                n=metrics["n"],
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
    text = "\n".join(lines)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    _write_json(destination.with_suffix(".json"), payload)
    return payload
