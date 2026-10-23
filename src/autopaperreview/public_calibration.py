"""Report-only comparison of the declared ICLR formula to public human scores.

The tool fetches public OpenReview manuscripts and official ratings, runs the
local pipeline, and writes agreement metrics. It does not fit weights, caps,
or bins, and it does not write those metrics back into the rating formula.

Cache directories and report paths must sit outside the git checkout or under
a gitignored review directory (``workdir/``, ``reviews/``, ``papers/``,
``uploads/``, or ``.autopaperreview/``). Forum ids, PDFs, reviews, and scores
stay in that cache.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import subprocess
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .adapters import digest_packet_text, load_review_adapter
from .anchors import LOCKED_HOLISTIC_SAMPLES
from .pipeline import PipelineError, run_pipeline
from .rating import snap_down
from .rubrics import COEFFICIENT_SET, ICLR_BINS

API_ROOT = "https://api2.openreview.net"
USER_AGENT = "AutoPaperReview-calibration-eval"
LOCAL_CACHE_PREFIXES = ("workdir", "reviews", "papers", "uploads", ".autopaperreview")
SPREAD_STDEV_FLOOR = 1.0
SPREAD_UNIQUE_FLOOR = 3
AGENT_ADAPTER = "autopaperreview.adapters:AgentJudgmentAdapter"
MODEL_ADAPTER = "autopaperreview.model_exec:OpenAICompatibleAdapter"

# Public venue listings used only when ``/notes?id=`` answers with a browser
# challenge. These are venue names, not submission ids.
VENUE_LISTINGS: tuple[dict[str, str], ...] = (
    {
        "label": "ICLR 2024 oral",
        "term": '"ICLR 2024 oral"',
        "group": "ICLR.cc/2024/Conference",
        "invitation": "ICLR.cc/2024/Conference/-/Submission",
    },
    {
        "label": "ICLR 2024 spotlight",
        "term": '"ICLR 2024 spotlight"',
        "group": "ICLR.cc/2024/Conference",
        "invitation": "ICLR.cc/2024/Conference/-/Submission",
    },
    {
        "label": "ICLR 2024 poster",
        "term": '"ICLR 2024 poster"',
        "group": "ICLR.cc/2024/Conference",
        "invitation": "ICLR.cc/2024/Conference/-/Submission",
    },
    {
        "label": "ICLR 2024 rejected",
        "term": '"Submitted to ICLR 2024"',
        "group": "ICLR.cc/2024/Conference",
        "invitation": "ICLR.cc/2024/Conference/-/Submission",
    },
    {
        "label": "ICLR 2025 oral",
        "term": '"ICLR 2025 oral"',
        "group": "ICLR.cc/2025/Conference",
        "invitation": "ICLR.cc/2025/Conference/-/Submission",
    },
    {
        "label": "ICLR 2025 spotlight",
        "term": '"ICLR 2025 spotlight"',
        "group": "ICLR.cc/2025/Conference",
        "invitation": "ICLR.cc/2025/Conference/-/Submission",
    },
    {
        "label": "ICLR 2025 poster",
        "term": '"ICLR 2025 Poster"',
        "group": "ICLR.cc/2025/Conference",
        "invitation": "ICLR.cc/2025/Conference/-/Submission",
    },
    {
        "label": "ICLR 2025 rejected",
        "term": '"Submitted to ICLR 2025"',
        "group": "ICLR.cc/2025/Conference",
        "invitation": "ICLR.cc/2025/Conference/-/Submission",
    },
)

CALIBRATION_ROUTES: tuple[tuple[str, str, str, str], ...] = (
    ("M0", "Structured closed-book review", "prompt", "closed_book_v1.md"),
    ("M2", "Metric and protocol review", "prompt", "metric_protocol_v1.md"),
    ("M5", "Reproducibility audit", "prompt", "reproducibility_v1.md"),
    ("M8", "First-read clarity review", "prompt", "first_read_v1.md"),
)

TextExtractor = Callable[[Path], str]
ArxivFinder = Callable[[str], tuple[str, bytes] | None]
Sleeper = Callable[[float], None]


class PublicCalibrationError(ValueError):
    """The calibration eval cannot continue. The rating formula is unchanged."""


class ChallengeRequired(PublicCalibrationError):
    """The public API asked for a browser check on this route."""


class ManifestPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forum_id: str = Field(pattern=r"^[A-Za-z0-9_-]{4,64}$")
    decision: str | None = Field(default=None, pattern=r"^(accept|reject)$")
    listing: str | None = None


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    papers: list[ManifestPaper] = Field(min_length=1)


class ScoreRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    forum_id: str
    title: str
    decision: str
    human_ratings: list[int]
    our_bin: int
    pdf_source: str
    cap_ids: list[str] = Field(default_factory=list)
    weighted: float | None = None
    gate: float | None = None
    raw: float | None = None

    @property
    def human_mean(self) -> float:
        return sum(self.human_ratings) / len(self.human_ratings)


_STATUS_LINE = re.compile(
    r"^\s*(?:Published as a conference paper|Under review as a conference paper|"
    r"Accepted (?:to|at) (?:ICLR|NeurIPS|ICML|CVPR|ACL))\b",
    re.IGNORECASE,
)


def strip_venue_status_lines(text: str) -> str:
    """Drop camera-ready banner lines so a public PDF cannot leak the decision."""
    kept = [line for line in text.splitlines() if not _STATUS_LINE.search(line)]
    body = "\n".join(kept).strip()
    return body + ("\n" if body else "")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def assert_local_only(path: Path, *, root: Path | None = None) -> Path:
    """Refuse a path that would land inside the public checkout."""
    resolved = path.expanduser().resolve()
    checkout = (root or repo_root()).resolve()
    if not resolved.is_relative_to(checkout):
        return resolved
    relative = resolved.relative_to(checkout)
    if relative.parts and relative.parts[0] in LOCAL_CACHE_PREFIXES:
        return resolved
    completed = subprocess.run(
        ["git", "-C", str(checkout), "check-ignore", "-q", "--", str(resolved)],
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return resolved
    raise PublicCalibrationError(
        f"{resolved} is inside the repository. Write the cache and the report under "
        "workdir/ (or another gitignored review directory), not into the checkout."
    )


def load_manifest(path: Path) -> Manifest:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".toml":
        payload = tomllib.loads(text)
    else:
        payload = json.loads(text)
    try:
        manifest = Manifest.model_validate(payload)
    except ValidationError as exc:
        raise PublicCalibrationError(f"manifest is invalid: {exc}") from exc
    ids = [item.forum_id for item in manifest.papers]
    if len(ids) != len(set(ids)):
        raise PublicCalibrationError("manifest forum ids must be unique")
    return manifest


def content_value(note: dict[str, Any], key: str) -> Any:
    content = note.get("content") or {}
    value = content.get(key)
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def conference_group(venueid: str) -> str:
    for suffix in (
        "/Rejected_Submission",
        "/Withdrawn_Submission",
        "/Desk_Rejected_Submission",
    ):
        if venueid.endswith(suffix):
            return venueid[: -len(suffix)]
    return venueid


def submission_number(note: dict[str, Any]) -> int | None:
    number = note.get("number")
    if isinstance(number, int):
        return number
    if isinstance(number, str) and number.isdigit():
        return int(number)
    for invitation in note.get("invitations") or []:
        marker = "/Submission"
        if marker not in invitation:
            continue
        tail = invitation.split(marker, 1)[1]
        digits = []
        for character in tail:
            if character.isdigit():
                digits.append(character)
            else:
                break
        if digits:
            return int("".join(digits))
    return None


def decision_of(note: dict[str, Any], override: str | None = None) -> str | None:
    if override in {"accept", "reject"}:
        return override
    venueid = str(content_value(note, "venueid") or "")
    venue = str(content_value(note, "venue") or "").lower()
    if "Withdrawn" in venueid or "withdrawn" in venue:
        return None
    if "Desk_Rejected" in venueid or "desk reject" in venue:
        return "reject"
    if "Reject" in venueid or "reject" in venue:
        return "reject"
    if venueid.endswith("/Conference") or any(token in venue for token in ("poster", "spotlight", "oral")):
        return "accept"
    return None


def is_official_review(note: dict[str, Any]) -> bool:
    invitations = list(note.get("invitations") or [])
    if isinstance(note.get("invitation"), str):
        invitations.append(note["invitation"])
    text = " ".join(invitations)
    if "Official_Review" not in text:
        return False
    if "Meta_Review" in text:
        return False
    return True


def parse_rating(value: Any) -> int | None:
    """Parse a public ICLR rating into a bin. Unknown scales are ignored."""
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        if float(number) != float(value):
            return None
        return number if number in ICLR_BINS else None
    if isinstance(value, str):
        head = value.strip().split(":", 1)[0].strip()
        if head.isdigit():
            number = int(head)
            return number if number in ICLR_BINS else None
    return None


def latest_official_reviews(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, ...], dict[str, Any]] = {}
    for note in notes:
        if not is_official_review(note):
            continue
        signatures = tuple(str(item) for item in (note.get("signatures") or [note.get("id")]))
        current = best.get(signatures)
        if current is None or int(note.get("tmdate") or 0) >= int(current.get("tmdate") or 0):
            best[signatures] = note
    return list(best.values())


def human_ratings(notes: list[dict[str, Any]]) -> tuple[list[int], list[str]]:
    ratings: list[int] = []
    labels: list[str] = []
    for note in latest_official_reviews(notes):
        raw = content_value(note, "rating")
        parsed = parse_rating(raw)
        if parsed is None:
            continue
        ratings.append(parsed)
        labels.append(str(raw if not isinstance(raw, dict) else raw.get("value")))
    return ratings, labels


SUBSCORE_AXES = ("soundness", "presentation", "contribution")
SMALL_SCALE = {1, 2, 3, 4}


def parse_prefixed_int(value: Any, *, allowed: set[int]) -> int | None:
    """Leading integer on a public review field, kept only when it is in ``allowed``."""
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and float(int(value)) == float(value):
        number = int(value)
        return number if number in allowed else None
    if isinstance(value, str):
        head = value.strip().split(":", 1)[0].strip().split(" ", 1)[0]
        if head.isdigit():
            number = int(head)
            return number if number in allowed else None
    return None


def human_subscores(notes: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Official 1–4 soundness, presentation, and contribution, when the note has them."""
    found = {axis: [] for axis in SUBSCORE_AXES}
    for note in latest_official_reviews(notes):
        for axis in SUBSCORE_AXES:
            parsed = parse_prefixed_int(content_value(note, axis), allowed=SMALL_SCALE)
            if parsed is not None:
                found[axis].append(parsed)
    return found


def nearest_bin(value: float) -> int:
    """Closest bin on 1/3/5/6/8/10. A tie stays on the lower bin."""
    best = ICLR_BINS[0]
    best_distance = abs(value - best)
    for bin_value in ICLR_BINS[1:]:
        distance = abs(value - bin_value)
        if distance < best_distance:
            best = bin_value
            best_distance = distance
    return best


def _pearson(left: list[float], right: list[float]) -> float | None:
    count = len(left)
    if count < 2 or count != len(right):
        return None
    mean_left = sum(left) / count
    mean_right = sum(right) / count
    numerator = sum((x - mean_left) * (y - mean_right) for x, y in zip(left, right, strict=True))
    left_scale = sum((x - mean_left) ** 2 for x in left) ** 0.5
    right_scale = sum((y - mean_right) ** 2 for y in right) ** 0.5
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(values):
        end = cursor
        while end + 1 < len(values) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        average = (cursor + end) / 2 + 1
        for index in range(cursor, end + 1):
            ranks[order[index]] = average
        cursor = end + 1
    return ranks


def spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    return _pearson(_average_ranks(left), _average_ranks(right))


def kendall_tau(left: list[float], right: list[float]) -> float | None:
    """Kendall tau-b. Ties in one side only do not count as concordant or discordant."""
    count = len(left)
    if count < 2 or count != len(right):
        return None
    concordant = 0
    discordant = 0
    ties_left = 0
    ties_right = 0
    for index in range(count):
        for other in range(index + 1, count):
            left_gap = left[other] - left[index]
            right_gap = right[other] - right[index]
            if left_gap == 0 and right_gap == 0:
                continue
            if left_gap == 0:
                ties_left += 1
            elif right_gap == 0:
                ties_right += 1
            elif left_gap * right_gap > 0:
                concordant += 1
            else:
                discordant += 1
    denominator = (concordant + discordant + ties_left) * (concordant + discordant + ties_right)
    if denominator <= 0:
        return None
    return (concordant - discordant) / (denominator ** 0.5)


def confusion_counts(rows: list[ScoreRow]) -> dict[str, Any]:
    """Human nearest-bin against our venue bin. Both axes are 1/3/5/6/8/10."""
    labels = [str(item) for item in ICLR_BINS]
    counts = {human: {ours: 0 for ours in labels} for human in labels}
    for row in rows:
        human = str(nearest_bin(row.human_mean))
        ours = str(int(row.our_bin))
        if human not in counts or ours not in counts[human]:
            continue
        counts[human][ours] += 1
    return {
        "human_rule": "nearest bin on 1/3/5/6/8/10; a tie stays on the lower bin",
        "bins": list(ICLR_BINS),
        "counts": counts,
    }


def roc_auc(scores: list[float], labels: list[int]) -> float | None:
    """AUC of ``scores`` for accept (1) versus reject (0). Ties count as 0.5."""
    positive = [score for score, label in zip(scores, labels, strict=True) if label == 1]
    negative = [score for score, label in zip(scores, labels, strict=True) if label == 0]
    if not positive or not negative:
        return None
    wins = 0.0
    for high in positive:
        for low in negative:
            if high > low:
                wins += 1
            elif high == low:
                wins += 0.5
    return wins / (len(positive) * len(negative))


def summarize(rows: list[ScoreRow]) -> dict[str, Any]:
    """Agreement metrics. This function has no weights and does not update any."""
    ours = [float(row.our_bin) for row in rows]
    means = [row.human_mean for row in rows]
    labels = [1 if row.decision == "accept" else 0 for row in rows]
    absolute = [abs(row.our_bin - row.human_mean) for row in rows]
    bin_hits = [row.our_bin == nearest_bin(row.human_mean) for row in rows]
    snap_hits = [row.our_bin == snap_down(row.human_mean) for row in rows]
    reviewer_hits: list[float] = []
    for row in rows:
        if not row.human_ratings:
            continue
        reviewer_hits.append(sum(rating == row.our_bin for rating in row.human_ratings) / len(row.human_ratings))
    unique = sorted(set(row.our_bin for row in rows))
    stdev = statistics.stdev(ours) if len(ours) >= 2 else None
    clustered = bool(rows) and (len(unique) < SPREAD_UNIQUE_FLOOR or stdev is None or stdev < SPREAD_STDEV_FLOOR)
    accept_scores = [row.our_bin for row in rows if row.decision == "accept"]
    reject_scores = [row.our_bin for row in rows if row.decision == "reject"]

    def _mean(values: list[float] | list[int]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    return {
        "coefficient_set": COEFFICIENT_SET,
        "fitted": False,
        "n": len(rows),
        "n_accept": len(accept_scores),
        "n_reject": len(reject_scores),
        "spearman": spearman(ours, means),
        "kendall": kendall_tau(ours, means),
        "mae": _mean(absolute),
        "signed_error": _mean([row.our_bin - row.human_mean for row in rows]),
        "confusion": confusion_counts(rows),
        "bin_agreement": _mean([1.0 if hit else 0.0 for hit in bin_hits]) if rows else None,
        "snap_bin_agreement": _mean([1.0 if hit else 0.0 for hit in snap_hits]) if rows else None,
        "reviewer_bin_agreement": _mean(reviewer_hits),
        "accept_reject_auc": roc_auc(ours, labels),
        "spread_stdev": stdev,
        "spread_range": (max(ours) - min(ours)) if ours else None,
        "unique_bins": unique,
        "clustered": clustered,
        "our_mean_accept": _mean(accept_scores),
        "our_mean_reject": _mean(reject_scores),
        "spread_stdev_floor": SPREAD_STDEV_FLOOR,
        "spread_unique_floor": SPREAD_UNIQUE_FLOOR,
        "bin_rule": "nearest bin on 1/3/5/6/8/10; a tie stays on the lower bin",
        "mae_rule": "mean absolute error of the venue bin against the mean official rating",
        "auc_rule": "Mann-Whitney AUC of the venue bin for accept versus reject",
    }


def _confusion_markdown(confusion: dict[str, Any]) -> str:
    bins = [str(item) for item in confusion.get("bins") or ICLR_BINS]
    counts = confusion.get("counts") or {}
    header = "| Human bin \\ Our bin | " + " | ".join(bins) + " |"
    rule = "| --- | " + " | ".join("---" for _ in bins) + " |"
    lines = [header, rule]
    for human in bins:
        row = counts.get(human) or {}
        cells = " | ".join(str(int(row.get(ours) or 0)) for ours in bins)
        lines.append(f"| {human} | {cells} |")
    return "\n".join(lines)


def render_report(rows: list[ScoreRow], metrics: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.4f}"
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    lines = [
        "# Public calibration report",
        "",
        f"Coefficient set: `{metrics['coefficient_set']}`.",
        "This report does not fit weights, caps, or bins. The declared formula is unchanged.",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Papers | {metrics['n']} ({metrics['n_accept']} accept / {metrics['n_reject']} reject) |",
        f"| Spearman (our bin vs human mean) | {cell(metrics['spearman'])} |",
        f"| Kendall tau-b (our bin vs human mean) | {cell(metrics.get('kendall'))} |",
        f"| MAE vs mean official rating | {cell(metrics['mae'])} |",
        f"| Signed error (ours minus human mean) | {cell(metrics['signed_error'])} |",
        f"| Bin agreement (nearest) | {cell(metrics['bin_agreement'])} |",
        f"| Bin agreement (snap_down of the mean) | {cell(metrics['snap_bin_agreement'])} |",
        f"| Reviewer bin agreement | {cell(metrics['reviewer_bin_agreement'])} |",
        f"| Accept/reject AUC | {cell(metrics['accept_reject_auc'])} |",
        f"| Score spread (sample stdev) | {cell(metrics['spread_stdev'])} |",
        f"| Score range | {cell(metrics['spread_range'])} |",
        f"| Unique bins | {cell(metrics['unique_bins'])} |",
        f"| Clustered | {metrics['clustered']} |",
        f"| Mean bin on accepts | {cell(metrics['our_mean_accept'])} |",
        f"| Mean bin on rejects | {cell(metrics['our_mean_reject'])} |",
        "",
        "Bin agreement is the fraction of papers whose venue bin equals the nearest",
        "public-rating mean on 1/3/5/6/8/10. A distance tie stays on the lower bin.",
        "Reviewer bin agreement is the average fraction of official ratings equal to our bin.",
        "",
        "Confusion counts use the nearest public-rating bin as the row and our venue bin as the column.",
        "",
        _confusion_markdown(metrics.get("confusion") or {}),
        "",
        f"Clustered is true when fewer than {SPREAD_UNIQUE_FLOOR} distinct bins appear",
        f"or the sample standard deviation is below {SPREAD_STDEV_FLOOR}.",
        "That flag is a diagnostic, not a threshold the formula was tuned to.",
        "",
        "## Per paper",
        "",
        "| Forum | Decision | PDF | Human ratings | Human mean | Human bin | Our bin | Abs error | Caps |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {forum} | {decision} | {pdf} | {ratings} | {mean:.4f} | {human_bin} | {ours} | {error:.4f} | {caps} |".format(
                forum=row.forum_id,
                decision=row.decision,
                pdf=row.pdf_source,
                ratings=", ".join(str(item) for item in row.human_ratings),
                mean=row.human_mean,
                human_bin=nearest_bin(row.human_mean),
                ours=row.our_bin,
                error=abs(row.our_bin - row.human_mean),
                caps=", ".join(row.cap_ids),
            )
        )
    lines.extend(
        [
            "",
            "## Titles",
            "",
        ]
    )
    for row in rows:
        lines.append(f"- `{row.forum_id}` ({row.project_id}): {row.title}")
    lines.extend(
        [
            "",
            "## Operator notes",
            "",
            "Failure patterns and recommendations belong here. Do not copy them back into weights.",
            "",
        ]
    )
    return "\n".join(lines)


class HttpOpenReviewClient:
    """Public OpenReview HTTP client. No credentials."""

    def __init__(self, *, timeout: float = 60, sleeper: Sleeper | None = None) -> None:
        self.timeout = timeout
        self._sleep = sleeper or (lambda _seconds: None)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = API_ROOT + path + ("?" + query if query else "")
        body = self._read(url, accept="application/json")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise PublicCalibrationError("OpenReview returned a non-JSON notes payload") from exc
        if not isinstance(payload, dict):
            raise PublicCalibrationError("OpenReview notes payload was not an object")
        self._sleep(0.2)
        return payload

    def get_bytes(self, url: str) -> bytes:
        body = self._read(url, accept="application/pdf,*/*")
        self._sleep(0.2)
        return body

    def _read(self, url: str, *, accept: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            if exc.code == 403 or b"ChallengeRequired" in payload:
                raise ChallengeRequired("OpenReview asked for a browser check on this route") from exc
            raise PublicCalibrationError(f"OpenReview HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise PublicCalibrationError(f"OpenReview request failed: {exc.reason}") from exc
        if b"ChallengeRequired" in payload_head(body) or body.lstrip()[:1] == b"<":
            if b"Verifying your browser" in body or b"ChallengeRequired" in body or b"<!doctype" in body[:80].lower():
                raise ChallengeRequired("OpenReview asked for a browser check on this route")
        return body


def payload_head(body: bytes) -> bytes:
    return body[:400]


def _normalize_title(value: str) -> str:
    folded = value.casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def default_arxiv_finder(title: str) -> tuple[str, bytes] | None:
    """Download an arXiv PDF only when the title matches the submission title."""
    import time

    time.sleep(3.0)
    query = urllib.parse.quote(f'ti:"{title.replace(chr(34), "")}"')
    url = "https://export.arxiv.org/api/query?search_query=" + query + "&max_results=5"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
    except urllib.error.URLError as exc:
        raise PublicCalibrationError(f"arXiv request failed: {exc.reason}") from exc
    root = ET.fromstring(payload)
    namespace = {"a": "http://www.w3.org/2005/Atom"}
    target = _normalize_title(title)
    for entry in root.findall("a:entry", namespace):
        found = entry.find("a:title", namespace)
        if found is None or found.text is None:
            continue
        if _normalize_title(found.text) != target:
            continue
        pdf_href = None
        arxiv_id = None
        identifier = entry.find("a:id", namespace)
        if identifier is not None and identifier.text:
            arxiv_id = identifier.text.rstrip("/").split("/")[-1]
        for link in entry.findall("a:link", namespace):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_href = link.attrib.get("href")
        if pdf_href is None or arxiv_id is None:
            continue
        pdf_request = urllib.request.Request(pdf_href, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(pdf_request, timeout=120) as response:
            body = response.read()
        if not body.startswith(b"%PDF"):
            raise PublicCalibrationError("arXiv response was not a PDF")
        return arxiv_id, body
    return None


def extract_pdf_text(path: Path) -> str:
    if shutil.which("pdftotext"):
        completed = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.decode("utf-8", errors="replace")
    try:
        import pypdfium2
    except ImportError:
        pypdfium2 = None
    if pypdfium2 is not None:
        document = pypdfium2.PdfDocument(str(path))
        try:
            pages = []
            for index in range(len(document)):
                page = document[index]
                text_page = page.get_textpage()
                pages.append(text_page.get_text_bounded())
            return "\n\n".join(pages)
        finally:
            document.close()
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PublicCalibrationError(
            "extracting PDF text needs pdftotext, pypdfium2, or pypdf"
        ) from exc
    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _direct_bundle(client: Any, forum_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    try:
        by_id = client.get_json("/notes", {"id": forum_id})
        by_forum = client.get_json("/notes", {"forum": forum_id, "limit": 1000})
    except ChallengeRequired:
        return None
    notes = by_id.get("notes") or []
    submission = next(
        (note for note in notes if note.get("id") == forum_id or note.get("forum") == forum_id),
        None,
    )
    if submission is None or not content_value(submission, "title"):
        return None
    return submission, list(by_forum.get("notes") or [])


def _search_submission(
    client: Any,
    forum_id: str,
    *,
    listing: str | None,
    max_pages: int,
) -> dict[str, Any]:
    listings = VENUE_LISTINGS
    if listing:
        listings = tuple(item for item in VENUE_LISTINGS if item["label"] == listing)
        if not listings:
            known = ", ".join(item["label"] for item in VENUE_LISTINGS)
            raise PublicCalibrationError(f"unknown listing {listing!r}; expected one of: {known}")
    for spec in listings:
        offset = 0
        seen: set[str] = set()
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "term": spec["term"],
                "group": spec["group"],
                "invitation": spec["invitation"],
                "limit": 50,
                "offset": offset,
            }
            data = client.get_json("/notes/search", params)
            notes = data.get("notes") or []
            if not notes:
                break
            fresh = False
            for note in notes:
                note_id = str(note.get("id") or "")
                if note_id in seen:
                    continue
                fresh = True
                seen.add(note_id)
                if note.get("forum") == forum_id or note.get("id") == forum_id:
                    if content_value(note, "title"):
                        return note
            if not fresh or len(notes) < 50:
                break
            offset += 50
    raise PublicCalibrationError(
        "could not resolve that forum id from the public notes API or the ICLR venue listings"
    )


def _search_reviews(client: Any, submission: dict[str, Any]) -> list[dict[str, Any]]:
    number = submission_number(submission)
    venueid = str(content_value(submission, "venueid") or "")
    group = conference_group(venueid)
    forum_id = str(submission.get("forum") or submission.get("id") or "")
    if number is None or not group:
        raise PublicCalibrationError("submission note is missing a number or venue id")
    invitation = f"{group}/Submission{number}/-/Official_Review"
    subgroup = f"{group}/Submission{number}"
    collected: list[dict[str, Any]] = []
    for term in ("the", "a"):
        by_invitation = client.get_json(
            "/notes/search",
            {"term": term, "invitation": invitation, "limit": 50},
        )
        collected.extend(by_invitation.get("notes") or [])
        by_group = client.get_json(
            "/notes/search",
            {"term": term, "group": subgroup, "limit": 100},
        )
        collected.extend(by_group.get("notes") or [])
    return [note for note in collected if note.get("forum") == forum_id]


def _openreview_pdf_urls(forum_id: str, pdf_path: str | None) -> list[str]:
    urls = [
        API_ROOT + "/pdf?id=" + forum_id,
        "https://openreview.net/" + "pdf?id=" + forum_id,
    ]
    if pdf_path:
        path = pdf_path if pdf_path.startswith("/") else "/" + pdf_path
        urls.append(API_ROOT + path)
        urls.append("https://openreview.net" + path)
    return urls


def download_pdf(
    client: Any,
    submission: dict[str, Any],
    *,
    pdf_fallback: str,
    arxiv_finder: ArxivFinder,
) -> tuple[str, bytes]:
    forum_id = str(submission.get("forum") or submission.get("id"))
    pdf_path = content_value(submission, "pdf")
    pdf_path = str(pdf_path) if isinstance(pdf_path, str) else None
    challenged = False
    for url in _openreview_pdf_urls(forum_id, pdf_path):
        try:
            body = client.get_bytes(url)
        except ChallengeRequired:
            challenged = True
            continue
        except PublicCalibrationError:
            continue
        if body.startswith(b"%PDF"):
            return "openreview", body
    if pdf_fallback != "arxiv":
        if challenged:
            raise ChallengeRequired(
                "OpenReview PDF route asked for a browser check. Re-run with --pdf-fallback arxiv "
                "or pass a client that can fetch the PDF bytes."
            )
        raise PublicCalibrationError("OpenReview did not return a PDF")
    title = str(content_value(submission, "title") or "")
    found = arxiv_finder(title)
    if found is None:
        raise PublicCalibrationError("OpenReview PDF was unavailable and arXiv had no exact title match")
    arxiv_id, body = found
    if not body.startswith(b"%PDF"):
        raise PublicCalibrationError("fallback PDF bytes were not a PDF")
    return "arxiv:" + arxiv_id, body


def paper_dir(cache: Path, forum_id: str) -> Path:
    return cache / "papers" / forum_id


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_manifest(
    manifest_path: Path,
    cache: Path,
    *,
    pdf_fallback: str = "arxiv",
    max_pages: int = 40,
    client: Any | None = None,
    text_extractor: TextExtractor | None = None,
    arxiv_finder: ArxivFinder | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Download public submissions into ``cache``. Human ratings stay under ``heldout/``."""
    cache = assert_local_only(cache, root=root)
    manifest = load_manifest(manifest_path)
    http = client or HttpOpenReviewClient()
    extract = text_extractor or extract_pdf_text
    finder = arxiv_finder or default_arxiv_finder
    cache.mkdir(parents=True, exist_ok=True)
    statuses: list[dict[str, Any]] = []
    for item in manifest.papers:
        status = _fetch_one(
            http,
            item,
            cache,
            pdf_fallback=pdf_fallback,
            max_pages=max_pages,
            text_extractor=extract,
            arxiv_finder=finder,
        )
        statuses.append(status)
        _write_json(paper_dir(cache, item.forum_id) / "status.json", status)
    _write_json(cache / "fetch_status.json", statuses)
    return statuses


def materialize_preloaded(
    cache: Path,
    item: ManifestPaper,
    submission: dict[str, Any],
    reviews: list[dict[str, Any]],
    *,
    pdf_fallback: str = "arxiv",
    client: Any | None = None,
    text_extractor: TextExtractor | None = None,
    arxiv_finder: ArxivFinder | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Write one paper cache from notes the caller already holds.

    This skips the venue-listing scan. Human ratings still go only under
    ``heldout/``. The PDF fallback is the same as ``fetch_manifest``.
    """
    cache = assert_local_only(cache, root=root)
    http = client or HttpOpenReviewClient()
    status = _fetch_one(
        http,
        item,
        cache,
        pdf_fallback=pdf_fallback,
        max_pages=1,
        text_extractor=text_extractor or extract_pdf_text,
        arxiv_finder=arxiv_finder or default_arxiv_finder,
        preloaded=(submission, reviews),
    )
    _write_json(paper_dir(cache, item.forum_id) / "status.json", status)
    return status


def _fetch_one(
    client: Any,
    item: ManifestPaper,
    cache: Path,
    *,
    pdf_fallback: str,
    max_pages: int,
    text_extractor: TextExtractor,
    arxiv_finder: ArxivFinder,
    preloaded: tuple[dict[str, Any], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    destination = paper_dir(cache, item.forum_id)
    destination.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "forum_id": item.forum_id,
        "state": "error",
        "decision": item.decision,
        "title": None,
        "n_ratings": 0,
        "pdf_source": None,
        "text_chars": 0,
        "error": None,
    }
    try:
        if preloaded is not None:
            submission, reviews = preloaded
        else:
            direct = _direct_bundle(client, item.forum_id)
            if direct is None:
                submission = _search_submission(
                    client, item.forum_id, listing=item.listing, max_pages=max_pages
                )
                reviews = _search_reviews(client, submission)
            else:
                submission, reviews = direct
        decision = decision_of(submission, item.decision)
        title = str(content_value(submission, "title") or "")
        ratings, labels = human_ratings(reviews)
        status["title"] = title
        status["decision"] = decision
        status["n_ratings"] = len(ratings)
        if decision not in {"accept", "reject"}:
            raise PublicCalibrationError("public note has no accept/reject decision")
        if len(ratings) < 2:
            raise PublicCalibrationError("fewer than two official ratings on the 1/3/5/6/8/10 scale")
        pdf_source, pdf_bytes = download_pdf(
            client,
            submission,
            pdf_fallback=pdf_fallback,
            arxiv_finder=arxiv_finder,
        )
        pdf_path = destination / "manuscript.pdf"
        pdf_path.write_bytes(pdf_bytes)
        text = text_extractor(pdf_path).strip()
        if len(text) < 800:
            raise PublicCalibrationError("extracted manuscript text is too short to review")
        (destination / "manuscript.txt").write_text(text + "\n", encoding="utf-8")
        _write_json(destination / "submission.json", submission)
        heldout = destination / "heldout"
        heldout.mkdir(parents=True, exist_ok=True)
        _write_json(heldout / "reviews.json", reviews)
        _write_json(
            heldout / "human.json",
            {
                "decision": decision,
                "ratings": ratings,
                "rating_labels": labels,
                "subscores": human_subscores(reviews),
            },
        )
        status.update(
            {
                "state": "fetched",
                "pdf_source": pdf_source,
                "text_chars": len(text),
                "error": None,
            }
        )
    except (PublicCalibrationError, PipelineError, OSError, ValidationError) as exc:
        status["error"] = str(exc)
        status["state"] = "error"
    return status


def _prompt_version(filename: str) -> str:
    if filename in {"first_read_v1.md", "figure_grounded_v1.md", "contribution_audit_v1.md"}:
        return "1.0"
    return "1.1"


def _toml_string(value: str) -> str:
    if any(character in value for character in "\n\r\""):
        raise PublicCalibrationError("TOML string values cannot contain quotes or newlines")
    return f'"{value}"'


def _write_project(
    destination: Path,
    *,
    project_id: str,
    phase: str,
    adapter: str,
    network_policy: str,
    requires_network: bool,
    related_work: str = "off",
    base_url: str | None = None,
    model: str | None = None,
    api_key_env: str = "APR_MODEL_API_KEY",
    allow_remote: bool = False,
    coefficient_set: str = COEFFICIENT_SET,
    with_holistic: bool = False,
    cheap: bool = False,
    holistic_samples: int = LOCKED_HOLISTIC_SAMPLES,
) -> None:
    project = destination / "project"
    project.mkdir(parents=True, exist_ok=True)
    manuscript = strip_venue_status_lines((destination / "manuscript.txt").read_text(encoding="utf-8"))
    (project / "manuscript.txt").write_text(manuscript, encoding="utf-8")
    pdf = destination / "manuscript.pdf"
    if pdf.is_file():
        shutil.copyfile(pdf, project / "manuscript.pdf")
    prompts = project / "prompts"
    prompts.mkdir(exist_ok=True)
    from importlib.resources import files

    for _route_id, _name, _kind, filename in CALIBRATION_ROUTES:
        source = files("autopaperreview").joinpath("prompts", filename)
        (prompts / filename).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    routes = []
    for route_id, name, kind, filename in CALIBRATION_ROUTES:
        routes.append(
            "\n".join(
                [
                    "[[routes]]",
                    f'id = "{route_id}"',
                    f'name = "{name}"',
                    f'kind = "{kind}"',
                    f'prompt = "prompts/{filename}"',
                    f'prompt_version = "{_prompt_version(filename)}"',
                    "manuscript_access = true",
                    "network_access = false",
                    "",
                ]
            )
        )
    stages = [
        "[[stages]]",
        'id = "ingest"',
        'type = "ingest"',
        "",
        "[[stages]]",
        'id = "prompt_packets"',
        'type = "prompt_packet"',
        'depends_on = ["ingest"]',
        "[stages.params]",
        "include_source_text = true",
        "max_source_chars = 500000",
        "",
    ]
    if phase == "run":
        review_params = [
            "[[stages]]",
            'id = "review"',
            'type = "execute_review"',
            'depends_on = ["prompt_packets"]',
            "[stages.params]",
            f'adapter = "{adapter}"',
        ]
        if adapter == AGENT_ADAPTER:
            review_params.append('fixture_path = "judgment.json"')
        if adapter == MODEL_ADAPTER:
            if not base_url or not model:
                raise PublicCalibrationError(
                    "OpenAICompatibleAdapter requires --base-url and --model"
                )
            review_params.append(f"base_url = {_toml_string(base_url)}")
            review_params.append(f"model = {_toml_string(model)}")
            review_params.append(f"api_key_env = {_toml_string(api_key_env)}")
            review_params.append(f"allow_remote = {'true' if allow_remote else 'false'}")
            from .rubrics import FUSION_COEFFICIENT_SET

            include_holistic = with_holistic or cheap or coefficient_set == FUSION_COEFFICIENT_SET
            if cheap:
                judgment_names = ["holistic"]
            else:
                judgment_names = ["contributions", "ledger", "dimensions", "fatal"]
                if include_holistic:
                    judgment_names.append("holistic")
            review_params.append("judgments = [" + ", ".join(f'"{item}"' for item in judgment_names) + "]")
            if cheap:
                review_params.append("cheap = true")
            if include_holistic:
                review_params.append(f"holistic_samples = {int(holistic_samples)}")
        if requires_network:
            review_params.append("requires_network = true")
            review_params.append('network_scope = "fulltext"')
        if adapter == MODEL_ADAPTER:
            if cheap:
                route_lines = ['holistic = "M0"']
            else:
                route_lines = [
                    'contributions = "M0"',
                    'ledger = "M0"',
                    'dimensions = "M2"',
                    'fatal = "M2"',
                ]
                if include_holistic:
                    route_lines.append('holistic = "M0"')
            review_params.extend(["", "[stages.params.packet_routes]", *route_lines])
        review_params.append("")
        related_params = [
            "[[stages]]",
            'id = "related_work"',
            'type = "related_work_search"',
            'depends_on = ["ingest"]',
            "[stages.params]",
            'venue = "iclr2027"',
            f"mode = {_toml_string(related_work)}",
            'network_scope = "metadata"',
        ]
        project = destination / "project"
        findings = project / "related_work_findings.json"
        confirmation = project / "related_work_confirmation.json"
        if related_work == "agent":
            if not findings.is_file():
                raise PublicCalibrationError(
                    "related-work agent mode requires related_work_findings.json in the project directory"
                )
            related_params.append('findings_path = "related_work_findings.json"')
        if related_work == "live":
            related_params.append("requires_network = true")
        if confirmation.is_file():
            related_params.append('confirmation_path = "related_work_confirmation.json"')
        if adapter == MODEL_ADAPTER and related_work in {"agent", "live"}:
            related_params.append(f"confirmation_adapter = {_toml_string(MODEL_ADAPTER)}")
            related_params.append(f"confirmation_base_url = {_toml_string(base_url or '')}")
            related_params.append(f"confirmation_model = {_toml_string(model or '')}")
            related_params.append(f"confirmation_api_key_env = {_toml_string(api_key_env)}")
            related_params.append(
                f"confirmation_allow_remote = {'true' if allow_remote else 'false'}"
            )
        related_params.append("")
        review_params.extend(
            [
                *related_params,
                "[[stages]]",
                'id = "consensus"',
                'type = "consensus"',
                'depends_on = ["review", "related_work"]',
                "[stages.params]",
                "min_independent_routes = 1",
                "",
                "[[stages]]",
                'id = "rate"',
                'type = "rate"',
                'depends_on = ["consensus"]',
                "[stages.params]",
                f"coefficient_set = {_toml_string(coefficient_set)}",
                "",
            ]
        )
        stages.extend(review_params)
    toml_name = "review.prepare.toml" if phase == "prepare" else "review.toml"
    body = "\n".join(
        [
            "schema_version = 1",
            "",
            "[project]",
            f'id = "{project_id}"',
            'title = "Public calibration manuscript"',
            'source = "manuscript.txt"',
            'run_root = ".runs"',
            'default_language = "en"',
            f'network_policy = "{network_policy}"',
            "copy_source = false",
            "",
            *routes,
            *stages,
        ]
    )
    (project / toml_name).write_text(body, encoding="utf-8")


def project_id_for(forum_id: str) -> str:
    return "pub-" + forum_id


def prepare_cache(cache: Path, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Build prompt packets. Does not read held-out human ratings."""
    cache = assert_local_only(cache, root=root)
    results = []
    for status_path in sorted(cache.glob("papers/*/status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("state") not in {"fetched", "prepared", "awaiting_judgment", "scored"}:
            results.append({"forum_id": status.get("forum_id"), "state": status.get("state"), "error": status.get("error")})
            continue
        forum_id = str(status["forum_id"])
        destination = paper_dir(cache, forum_id)
        project_id = project_id_for(forum_id)
        _write_project(
            destination,
            project_id=project_id,
            phase="prepare",
            adapter=AGENT_ADAPTER,
            network_policy="deny",
            requires_network=False,
        )
        manifest = run_pipeline(destination / "project" / "review.prepare.toml")
        packet_dir = destination / "project" / ".runs" / manifest.run_id / "stages" / "prompt_packets"
        packets = {}
        for path in sorted(packet_dir.glob("*.md")):
            packets[path.stem] = digest_packet_text(path.read_text(encoding="utf-8"))
        if not packets:
            raise PublicCalibrationError(f"prompt packets were not written for {project_id}")
        _write_json(
            destination / "project" / "packet_index.json",
            {"project_id": project_id, "run_id": manifest.run_id, "packets": packets},
        )
        status["state"] = "prepared"
        status["error"] = None
        _write_json(status_path, status)
        results.append({"forum_id": forum_id, "state": "prepared", "project_id": project_id, "packets": sorted(packets)})
    return results


def _resolve_adapter(mode: str, adapter: str | None) -> str:
    if mode == "agent":
        return AGENT_ADAPTER
    if mode != "adapter":
        raise PublicCalibrationError("mode must be agent or adapter")
    spec = adapter or ""
    if not spec:
        raise PublicCalibrationError(
            "adapter mode requires --adapter module:attribute. "
            f"The bundled chat adapter is {MODEL_ADAPTER}."
        )
    try:
        load_review_adapter(spec)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise PublicCalibrationError(f"review adapter is not present: {exc}") from exc
    return spec


def run_cache(
    cache: Path,
    *,
    mode: str = "agent",
    adapter: str | None = None,
    network_policy: str = "deny",
    requires_network: bool = False,
    related_work: str = "off",
    base_url: str | None = None,
    model: str | None = None,
    api_key_env: str = "APR_MODEL_API_KEY",
    allow_remote: bool = False,
    coefficient_set: str = COEFFICIENT_SET,
    with_holistic: bool = False,
    cheap: bool = False,
    holistic_samples: int = LOCKED_HOLISTIC_SAMPLES,
    only_ids: set[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Run related-work search, execute_review, and rate. Agent mode waits for a judgment drop."""
    cache = assert_local_only(cache, root=root)
    if related_work not in {"off", "agent", "live"}:
        raise PublicCalibrationError("related_work must be off, agent, or live")
    if holistic_samples < 1 or holistic_samples > 8:
        raise PublicCalibrationError("holistic_samples must be an integer from 1 to 8")
    spec = _resolve_adapter(mode, adapter)
    if spec == MODEL_ADAPTER and (not base_url or not model):
        raise PublicCalibrationError("OpenAICompatibleAdapter requires --base-url and --model")
    if allow_remote and network_policy != "allow":
        raise PublicCalibrationError("a remote model endpoint requires --network-policy allow")
    if allow_remote and not requires_network:
        raise PublicCalibrationError("a remote model endpoint requires --requires-network")
    if related_work == "live" and network_policy == "deny":
        raise PublicCalibrationError("live related-work search requires --network-policy metadata_only or allow")
    if requires_network and network_policy != "allow":
        raise PublicCalibrationError("a network-using adapter requires --network-policy allow")
    awaiting = []
    scored = []
    errors = []
    for status_path in sorted(cache.glob("papers/*/status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("state") not in {"prepared", "awaiting_judgment", "scored"}:
            continue
        forum_id = str(status["forum_id"])
        if only_ids is not None and forum_id not in only_ids:
            continue
        destination = paper_dir(cache, forum_id)
        project = destination / "project"
        judgment = project / "judgment.json"
        if spec == AGENT_ADAPTER and not judgment.is_file():
            status["state"] = "awaiting_judgment"
            _write_json(status_path, status)
            index_path = project / "packet_index.json"
            awaiting.append(
                {
                    "forum_id": forum_id,
                    "project_id": project_id_for(forum_id),
                    "judgment_path": str(judgment),
                    "packet_index": str(index_path),
                    "packets_dir": str(project / ".runs"),
                }
            )
            continue
        try:
            _write_project(
                destination,
                project_id=project_id_for(forum_id),
                phase="run",
                adapter=spec,
                network_policy=network_policy,
                requires_network=requires_network,
                related_work=related_work,
                base_url=base_url,
                model=model,
                api_key_env=api_key_env,
                allow_remote=allow_remote,
                coefficient_set=coefficient_set,
                with_holistic=with_holistic,
                cheap=cheap,
                holistic_samples=holistic_samples,
            )
            manifest = run_pipeline(project / "review.toml")
            package_path = project / ".runs" / manifest.run_id / "stages" / "rate" / "package.json"
            package = json.loads(package_path.read_text(encoding="utf-8"))
            overall = package.get("overall_score") or {}
            if overall.get("coefficient_set") != coefficient_set:
                raise PublicCalibrationError("rate stage did not use the requested coefficient set")
            metadata = overall.get("metadata") or {}
            dimension_scores = {
                str(item.get("dimension")): item.get("score")
                for item in (package.get("dimension_scores") or [])
                if isinstance(item, dict) and item.get("dimension")
            }
            venue_bin = metadata.get("venue_bin", overall.get("value"))
            review_stage = manifest.stage("review")
            usage = {}
            if review_stage is not None:
                usage = dict((review_stage.metadata or {}).get("usage_totals") or {})
            score = {
                "project_id": project_id_for(forum_id),
                "forum_id": forum_id,
                "our_bin": int(venue_bin),
                "rank_score": metadata.get("rank_score", overall.get("value")),
                "cap_ids": list(metadata.get("cap_ids") or []),
                "caps": list(metadata.get("caps") or []),
                "weighted": metadata.get("weighted"),
                "gate": metadata.get("gate"),
                "raw": metadata.get("raw"),
                "capped": metadata.get("capped"),
                "holistic": metadata.get("holistic"),
                "samples": list(metadata.get("samples") or []),
                "mean_samples": list(metadata.get("mean_samples") or []),
                "place_samples": list(metadata.get("place_samples") or []),
                "count_samples": list(metadata.get("count_samples") or []),
                "isolate_samples": list(metadata.get("isolate_samples") or []),
                "placements": list(metadata.get("placements") or []),
                "poles": list(metadata.get("poles") or []),
                "ladder": list(metadata.get("ladder") or []),
                "candidates": metadata.get("candidates") or {},
                "drop_counts": dict((review_stage.metadata or {}).get("drop_counts") or {})
                if review_stage is not None
                else {},
                "fusion_variant": metadata.get("fusion_variant"),
                "dimensions": dimension_scores,
                "bands_after_ceiling": metadata.get("bands_after_ceiling") or {},
                "coefficient_set": overall.get("coefficient_set"),
                "usage_totals": usage,
                "pdf_source": status.get("pdf_source"),
                "title": status.get("title"),
            }
            _write_json(project / "our_score.json", score)
            status["state"] = "scored"
            status["error"] = None
            _write_json(status_path, status)
            scored.append({"forum_id": forum_id, "our_bin": score["our_bin"]})
        except (PublicCalibrationError, PipelineError, OSError, ValidationError, KeyError, TypeError) as exc:
            status["state"] = "error"
            status["error"] = str(exc)
            _write_json(status_path, status)
            errors.append({"forum_id": forum_id, "error": str(exc)})
    return {"scored": scored, "awaiting_judgment": awaiting, "errors": errors}


def collect_rows(cache: Path) -> list[ScoreRow]:
    rows: list[ScoreRow] = []
    for score_path in sorted(cache.glob("papers/*/project/our_score.json")):
        score = json.loads(score_path.read_text(encoding="utf-8"))
        human_path = score_path.parents[1] / "heldout" / "human.json"
        human = json.loads(human_path.read_text(encoding="utf-8"))
        decision = human.get("decision")
        if decision not in {"accept", "reject"}:
            continue
        ratings = [int(item) for item in human.get("ratings") or []]
        if len(ratings) < 2:
            continue
        rows.append(
            ScoreRow(
                project_id=str(score["project_id"]),
                forum_id=str(score["forum_id"]),
                title=str(score.get("title") or ""),
                decision=decision,
                human_ratings=ratings,
                our_bin=int(score["our_bin"]),
                pdf_source=str(score.get("pdf_source") or ""),
                cap_ids=[str(item) for item in score.get("cap_ids") or []],
                weighted=score.get("weighted"),
                gate=score.get("gate"),
                raw=score.get("raw"),
            )
        )
    return rows


def write_report(cache: Path, output: Path, *, root: Path | None = None) -> dict[str, Any]:
    cache = assert_local_only(cache, root=root)
    output = assert_local_only(output, root=root)
    rows = collect_rows(cache)
    if not rows:
        raise PublicCalibrationError("no scored papers with public ratings are in the cache")
    metrics = summarize(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(rows, metrics), encoding="utf-8")
    _write_json(cache / "report.json", {"metrics": metrics, "rows": [row.model_dump() for row in rows]})
    return metrics


def list_venue(
    listing: str,
    *,
    limit: int = 20,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Printable public submissions for one venue listing. Ratings are not included."""
    matches = [item for item in VENUE_LISTINGS if item["label"] == listing]
    if not matches:
        known = ", ".join(item["label"] for item in VENUE_LISTINGS)
        raise PublicCalibrationError(f"unknown listing {listing!r}; expected one of: {known}")
    spec = matches[0]
    http = client or HttpOpenReviewClient()
    found: list[dict[str, Any]] = []
    offset = 0
    seen: set[str] = set()
    while len(found) < limit:
        data = http.get_json(
            "/notes/search",
            {
                "term": spec["term"],
                "group": spec["group"],
                "invitation": spec["invitation"],
                "limit": min(50, limit),
                "offset": offset,
            },
        )
        notes = data.get("notes") or []
        if not notes:
            break
        fresh = False
        for note in notes:
            note_id = str(note.get("id") or "")
            if note_id in seen:
                continue
            fresh = True
            seen.add(note_id)
            title = content_value(note, "title")
            if not title:
                continue
            decision = decision_of(note)
            if decision not in {"accept", "reject"}:
                continue
            found.append(
                {
                    "forum_id": note.get("forum") or note.get("id"),
                    "decision": decision,
                    "listing": listing,
                    "number": submission_number(note),
                    "title": title,
                }
            )
            if len(found) >= limit:
                break
        if not fresh or len(notes) < 50:
            break
        offset += len(notes)
    return found
