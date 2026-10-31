"""Fixed, difficulty-stratified evaluation sets.

The ICLR dev list and the CVPR dev registration live in
``benchmarks/fixed_eval/manifest.json``. Status ``proposal`` (also accepted
as ``proposed``) means the membership is published for review and is not yet
locked. A run still has to
match the manifest digest. The private holdout is an HMAC list in that file.
Its salt and forum ids stay in a gitignored operator file.

Nothing here fits a rating weight.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import random
import re
from pathlib import Path
from typing import Any

from .hashing import hash_json, sha256_bytes

PROTOCOL = "fixed-eval-v1"
SELECTION_SEED = 20261030
DEV_PER_TIER = 48
HOLDOUT_PER_TIER = 6
AREA_CAP = 4
OPUS_SEED = 20261102
OPUS_PER_TIER = 36
OPUS_COLD_PER_TIER = 18
TIER_NAMES = ("clear_reject", "borderline_reject", "borderline_accept", "clear_accept")

# Official ICLR ratings are 1, 3, 5, 6, 8, and 10.
# 4.0 is the top of the historical clear-reject stratum.
# 5.5 sits between borderline reject (5) and borderline accept (6).
# 7.0 sits between borderline accept (6) and accept (8).
# The high end is inclusive so a mean of 10 stays in clear accept.
TIER_BOUNDS: tuple[tuple[str, float, float], ...] = (
    ("clear_reject", 0.0, 4.0),
    ("borderline_reject", 4.0, 5.5),
    ("borderline_accept", 5.5, 7.0),
    ("clear_accept", 7.0, 10.0),
)

FORECAST_K1 = {
    "cheap": True,
    "coefficient_set": "apr-fusion-v1",
    "holistic_mode": "forecast",
    "holistic_samples": 1,
    "rank_target": "rank_score",
    "related_work": "off",
}

RANK_BOOTSTRAP = {
    "method": "paper-level-linear-percentile",
    "percentiles": [0.025, 0.975],
    "resamples": 4000,
    "seed": 20261021,
    "source": "autopaperreview.rank_metrics",
}

PROMOTION_BOOTSTRAP = {
    "method": "paired-stratified-linear-percentile",
    "percentiles": [0.025, 0.975],
    "resamples": 4000,
    "seed": 20261029,
    "source": "benchmarks/promotion/paired_bootstrap.py",
    "strata": "the four ICLR tiers on this manifest",
}

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
EFFORT_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SEALED_CVPR = frozenset({"S24-C1", "S24-C2"})
_URL_MARK = "openreview.net/"


class FixedEvalError(ValueError):
    """The fixed set cannot be used for this run."""


def tier_for(mean: float) -> str:
    """Tier of an official-rating mean. The top bound is inclusive."""
    value = float(mean)
    for name, low, high in TIER_BOUNDS:
        if name == "clear_accept":
            if low <= value <= high:
                return name
        elif low <= value < high:
            return name
    raise FixedEvalError(f"mean {value} is outside 0 to 10")


def id_list_digest(ids: list[str] | tuple[str, ...]) -> str:
    """SHA-256 of the sorted unique id list plus a trailing newline.

    Same bytes as ``cvpr_sets.id_list_digest``.
    """
    payload = ("\n".join(sorted(set(ids))) + "\n").encode("utf-8")
    return sha256_bytes(payload)


def manifest_path() -> Path:
    from .public_calibration import repo_root

    return repo_root() / "benchmarks" / "fixed_eval" / "manifest.json"


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    target = path or manifest_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixedEvalError(f"fixed-eval manifest cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise FixedEvalError("fixed-eval manifest must be an object")
    return payload


def content_digest(manifest: dict[str, Any]) -> str:
    """Digest of every field except the stored digest itself."""
    body = {key: value for key, value in manifest.items() if key != "content_sha256"}
    return hash_json(body)


def opus_slice(
    papers: list[dict[str, Any]],
    *,
    seed: int = OPUS_SEED,
    per_tier: int = OPUS_PER_TIER,
) -> dict[str, list[str]]:
    """Standing Opus slice. Order is the shuffle order, not a sort.

    Inside each tier, forum ids are sorted and then shuffled with
    ``Random(f"opus-validation:{seed}:{tier}")``. The standing slice is the
    first ``per_tier`` ids in that order. The cold-start slice is the first
    ``OPUS_COLD_PER_TIER`` ids of the same list.
    """
    buckets: dict[str, list[str]] = {name: [] for name in TIER_NAMES}
    for paper in papers:
        tier = str(paper.get("tier") or "")
        if tier not in buckets:
            raise FixedEvalError(f"opus slice paper has an unknown tier {tier!r}")
        buckets[tier].append(str(paper["forum_id"]))
    drawn: dict[str, list[str]] = {}
    for tier in TIER_NAMES:
        ids = sorted(buckets[tier])
        rng = random.Random(f"opus-validation:{seed}:{tier}")
        rng.shuffle(ids)
        if len(ids) < per_tier:
            raise FixedEvalError(f"opus slice for {tier} needs {per_tier} papers, found {len(ids)}")
        drawn[tier] = ids[:per_tier]
    return drawn


def _mean_from_ratings(ratings: list[int]) -> float:
    return round(sum(ratings) / len(ratings), 4)


def _check_paper(paper: dict[str, Any], *, where: str) -> None:
    if not isinstance(paper, dict):
        raise FixedEvalError(f"{where} paper is not an object")
    forum_id = str(paper.get("forum_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", forum_id):
        raise FixedEvalError(f"{where} has a forum id that does not match the public id pattern")
    title = str(paper.get("title") or "")
    if not title.strip():
        raise FixedEvalError(f"{where} {forum_id} is missing a title")
    if _URL_MARK in title.lower() or _URL_MARK in str(paper.get("note") or "").lower():
        raise FixedEvalError(f"{where} {forum_id} contains a venue URL")
    ratings = paper.get("ratings")
    if not isinstance(ratings, list) or len(ratings) < 2 or not all(isinstance(item, int) for item in ratings):
        raise FixedEvalError(f"{where} {forum_id} needs at least two integer ratings")
    if any(item not in {1, 3, 5, 6, 8, 10} for item in ratings):
        raise FixedEvalError(f"{where} {forum_id} has a rating off the official scale")
    expected = _mean_from_ratings(ratings)
    stored = paper.get("mean_rating")
    if not isinstance(stored, (int, float)) or abs(float(stored) - expected) > 5e-5:
        raise FixedEvalError(f"{where} {forum_id} mean does not match its ratings")
    tier = paper.get("tier")
    if tier != tier_for(float(stored)):
        raise FixedEvalError(f"{where} {forum_id} tier does not match its mean")
    if paper.get("decision") not in {"accept", "reject"}:
        raise FixedEvalError(f"{where} {forum_id} decision must be accept or reject")


def _hmac_ids(salt: bytes, forum_ids: list[str]) -> tuple[list[str], str]:
    ordered = sorted(set(forum_ids))
    per_id = [hmac.new(salt, forum_id.encode("utf-8"), hashlib.sha256).hexdigest() for forum_id in ordered]
    listing = ("\n".join(ordered) + "\n").encode("utf-8")
    list_mac = hmac.new(salt, listing, hashlib.sha256).hexdigest()
    return sorted(per_id), list_mac


def assert_manifest(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refuse a manifest whose digest, tiers, or CVPR registration do not match."""
    payload = manifest if manifest is not None else load_manifest()
    if payload.get("protocol") != PROTOCOL:
        raise FixedEvalError(f"fixed-eval protocol must be {PROTOCOL}")
    if payload.get("status") not in {"proposal", "proposed", "locked"}:
        raise FixedEvalError("fixed-eval status must be proposal, proposed, or locked")
    stored = payload.get("content_sha256")
    if not isinstance(stored, str) or not HEX64_RE.fullmatch(stored):
        raise FixedEvalError("fixed-eval content digest is missing")
    if stored != content_digest(payload):
        raise FixedEvalError("fixed-eval content digest does not match the manifest")

    iclr = payload.get("iclr")
    if not isinstance(iclr, dict):
        raise FixedEvalError("fixed-eval ICLR block is missing")
    papers = iclr.get("papers")
    if not isinstance(papers, list):
        raise FixedEvalError("fixed-eval ICLR papers must be a list")
    seen: set[str] = set()
    counts = {name: 0 for name in TIER_NAMES}
    for index, paper in enumerate(papers):
        _check_paper(paper, where=f"iclr[{index}]")
        forum_id = str(paper["forum_id"])
        if forum_id in seen:
            raise FixedEvalError(f"duplicate ICLR forum id {forum_id}")
        seen.add(forum_id)
        counts[str(paper["tier"])] += 1
    if counts != {name: DEV_PER_TIER for name in TIER_NAMES}:
        raise FixedEvalError(f"ICLR dev tiers must be {DEV_PER_TIER} each, found {counts}")
    if payload.get("id_list_sha256") != id_list_digest([str(paper["forum_id"]) for paper in papers]):
        raise FixedEvalError("ICLR id-list digest does not match the dev papers")
    flags = payload.get("forecast_k1")
    if flags != FORECAST_K1:
        raise FixedEvalError("forecast-k1 flags do not match the locked command")
    if payload.get("bootstrap", {}).get("rank_metrics") != RANK_BOOTSTRAP:
        raise FixedEvalError("rank bootstrap settings do not match rank_metrics.py")

    holdout = payload.get("holdout")
    if not isinstance(holdout, dict):
        raise FixedEvalError("fixed-eval holdout block is missing")
    if holdout.get("role") != "private":
        raise FixedEvalError("holdout role must stay private")
    blob = json.dumps(holdout)
    if "forum_id" in blob or "title" in blob:
        raise FixedEvalError("the committed holdout block must not carry ids or titles")
    macs = holdout.get("id_hmac_sha256")
    per_tier = holdout.get("per_tier")
    if not isinstance(macs, list) or not all(isinstance(item, str) and HEX64_RE.fullmatch(item) for item in macs):
        raise FixedEvalError("holdout id HMACs must be 64 hex characters")
    if macs != sorted(macs):
        raise FixedEvalError("holdout id HMACs must be sorted")
    if not isinstance(holdout.get("list_hmac_sha256"), str) or not HEX64_RE.fullmatch(holdout["list_hmac_sha256"]):
        raise FixedEvalError("holdout list HMAC is missing")
    if not isinstance(per_tier, dict) or {str(key): int(value) for key, value in per_tier.items()} != {
        name: HOLDOUT_PER_TIER for name in TIER_NAMES
    }:
        raise FixedEvalError(f"private holdout must be {HOLDOUT_PER_TIER} papers in each tier")
    if len(macs) != sum(HOLDOUT_PER_TIER for _name in TIER_NAMES) or len(set(macs)) != len(macs):
        raise FixedEvalError("private holdout HMAC count does not match the tier counts")

    _assert_opus_block(payload.get("opus_validation"), papers)
    _assert_cvpr_block(payload.get("cvpr"))
    return payload


def _assert_opus_block(opus: Any, papers: list[dict[str, Any]]) -> None:
    if not isinstance(opus, dict):
        raise FixedEvalError("opus validation block is missing")
    if opus.get("seed") != OPUS_SEED:
        raise FixedEvalError(f"opus validation seed must be {OPUS_SEED}")
    if opus.get("papers_per_tier") != OPUS_PER_TIER or opus.get("cold_start_per_tier") != OPUS_COLD_PER_TIER:
        raise FixedEvalError(
            f"opus validation must be {OPUS_PER_TIER} per tier, with a {OPUS_COLD_PER_TIER}-paper cold start"
        )
    expected = opus_slice(papers, seed=OPUS_SEED, per_tier=OPUS_PER_TIER)
    stored = opus.get("paper_ids_by_tier")
    if stored != expected:
        raise FixedEvalError("opus slice does not match the seed and ordering")
    for tier in TIER_NAMES:
        if stored[tier][:OPUS_COLD_PER_TIER] != expected[tier][:OPUS_COLD_PER_TIER]:
            raise FixedEvalError("opus cold-start slice is not the prefix of the standing slice")


def _assert_cvpr_block(cvpr: Any) -> None:
    if not isinstance(cvpr, dict) or cvpr.get("separate_from_iclr") is not True:
        raise FixedEvalError("CVPR block must be separate from the ICLR papers")
    sealed = cvpr.get("sealed_not_used")
    if sealed != ["S24-C1", "S24-C2"]:
        raise FixedEvalError("sealed CVPR sets must be named and unused")
    sets = cvpr.get("sets")
    if not isinstance(sets, list):
        raise FixedEvalError("CVPR sets must be a list")
    by_id = {}
    for spec in sets:
        if not isinstance(spec, dict):
            raise FixedEvalError("CVPR set is not an object")
        set_id = str(spec.get("id") or "")
        if set_id in SEALED_CVPR:
            raise FixedEvalError(f"{set_id} is sealed and cannot be registered with a membership")
        by_id[set_id] = spec
        blob = json.dumps(spec)
        if "S24-C1" in blob and set_id == "S24-C1":
            raise FixedEvalError("sealed set membership must not be embedded")
    pilot = by_id.get("S24-P")
    second = by_id.get("S24-D2")
    if pilot is None or second is None:
        raise FixedEvalError("CVPR dev sets S24-P and S24-D2 must both be registered")
    pilot_ids = pilot.get("paper_ids")
    if not isinstance(pilot_ids, list) or len(pilot_ids) != 50:
        raise FixedEvalError("S24-P must list its 50 dev ids")
    if pilot.get("id_list_sha256") != id_list_digest([str(item) for item in pilot_ids]):
        raise FixedEvalError("S24-P id-list digest does not match its ids")
    if pilot.get("role") != "dev" or second.get("role") != "dev":
        raise FixedEvalError("S24-P and S24-D2 are dev sets")
    excluded = second.get("excluded_paper_ids")
    if excluded != ["cvpr2024-e71e5cd119bbc5797164fb0cd7fd94a4"]:
        raise FixedEvalError("S24-D2 exclusion does not match the leak-screened paper")
    if second.get("valid_n") != 49 or second.get("drawn_n") != 50:
        raise FixedEvalError("S24-D2 valid count must stay 49 after the one exclusion")
    if second.get("paper_ids") not in (None, []):
        raise FixedEvalError("S24-D2 ids are not stored in this manifest")
    if any(item in set(map(str, pilot_ids)) for item in excluded):
        raise FixedEvalError("the S24-D2 exclusion is also in S24-P")


def dev_papers(manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = assert_manifest(manifest)
    return list(payload["iclr"]["papers"])


def dev_ids(manifest: dict[str, Any] | None = None) -> list[str]:
    return [str(paper["forum_id"]) for paper in dev_papers(manifest)]


def promotion_iclr_ids() -> list[str]:
    """Ids the promotion ICLR arm must score. This replaces a fresh draw."""
    return dev_ids()


def assert_cvpr_stage(
    set_id: str | None,
    paper_ids: list[str] | tuple[str, ...],
    *,
    stage: str,
) -> dict[str, Any] | None:
    """Check a registered CVPR dev set or refuse a sealed set.

    Other set ids are ignored. ``stage`` is ``fetch`` or ``run``. A fetch of
    S24-D2 may still contain the leak-screened paper; the scored membership is
    the other 49. A run must already omit that paper.
    """
    if set_id not in {"S24-P", "S24-D2", *SEALED_CVPR}:
        return None
    if stage not in {"fetch", "run"}:
        raise FixedEvalError("CVPR fixed-set stage must be fetch or run")
    if set_id in SEALED_CVPR:
        assert_run_ids(paper_ids, kind=set_id)
    found = [str(item) for item in paper_ids]
    if set_id == "S24-D2" and stage == "fetch":
        payload = assert_manifest()
        excluded = {str(item) for item in _cvpr_spec(payload, "S24-D2").get("excluded_paper_ids") or []}
        if excluded & set(found):
            remainder = [item for item in found if item not in excluded]
            assert_run_ids(remainder, kind="S24-D2")
            return {
                "excluded_present": True,
                "set_id": "S24-D2",
                "stage": "fetch",
                "valid_n": len(set(remainder)),
            }
        assert_run_ids(found, kind="S24-D2")
        return {
            "excluded_present": False,
            "set_id": "S24-D2",
            "stage": "fetch",
            "valid_n": len(set(found)),
        }
    assert_run_ids(found, kind=set_id)
    return {"set_id": set_id, "stage": stage, "valid_n": len(set(found))}


def assert_run_ids(ids: list[str] | tuple[str, ...] | set[str], *, kind: str = "iclr-dev") -> dict[str, Any]:
    """Refuse a run whose ids are not the manifest set.

    ``kind`` is ``iclr-dev``, ``S24-P``, or ``S24-D2``. Sealed set names fail.
    """
    payload = assert_manifest()
    if kind in SEALED_CVPR:
        raise FixedEvalError(f"{kind} is sealed; this benchmark does not open it")
    found = [str(item) for item in ids]
    if kind == "iclr-dev":
        expected = dev_ids(payload)
        if sorted(found) != sorted(expected) or len(found) != len(set(found)):
            raise FixedEvalError(
                "ICLR run ids do not match the fixed dev set "
                f"({len(set(found))} ids, manifest {len(expected)})"
            )
        return payload
    if kind == "S24-P":
        expected = _cvpr_ids(payload, "S24-P")
        if sorted(found) != sorted(expected):
            raise FixedEvalError("S24-P run ids do not match the registered dev list")
        return payload
    if kind == "S24-D2":
        excluded = set(_cvpr_spec(payload, "S24-D2").get("excluded_paper_ids") or [])
        pilot = set(_cvpr_ids(payload, "S24-P"))
        if excluded & set(found):
            raise FixedEvalError("S24-D2 run includes the leak-screened exclusion")
        if pilot & set(found):
            raise FixedEvalError("S24-D2 run overlaps S24-P")
        if len(set(found)) != 49:
            raise FixedEvalError(f"S24-D2 run must contain 49 ids after the exclusion, found {len(set(found))}")
        if _cvpr_spec(payload, "S24-D2").get("id_list_sha256"):
            if id_list_digest(found) != _cvpr_spec(payload, "S24-D2")["id_list_sha256"]:
                raise FixedEvalError("S24-D2 id-list digest does not match the manifest")
        return payload
    raise FixedEvalError(f"unknown fixed set {kind}")


def _cvpr_spec(manifest: dict[str, Any], set_id: str) -> dict[str, Any]:
    for spec in manifest["cvpr"]["sets"]:
        if spec.get("id") == set_id:
            return spec
    raise FixedEvalError(f"CVPR set {set_id} is not registered")


def _cvpr_ids(manifest: dict[str, Any], set_id: str) -> list[str]:
    ids = _cvpr_spec(manifest, set_id).get("paper_ids") or []
    return [str(item) for item in ids]


def assert_model_recorded(model_id: str | None, effort: str | None) -> tuple[str, str]:
    """Require the model id and the effort. Neither is inferred."""
    model = (model_id or "").strip()
    level = (effort or "").strip()
    if not MODEL_ID_RE.fullmatch(model):
        raise FixedEvalError("a fixed-set run must record --model-id (letters, digits, ., _, -)")
    if not EFFORT_RE.fullmatch(level):
        raise FixedEvalError("a fixed-set run must record --effort (letters, digits, ., _, -)")
    return model, level


def assert_forecast_flags(
    *,
    cheap: bool,
    holistic_mode: str,
    holistic_samples: int,
    related_work: str,
    coefficient_set: str,
) -> None:
    """Refuse a fixed-set model run that is not the locked forecast-k1 command."""
    actual = {
        "cheap": bool(cheap),
        "coefficient_set": coefficient_set,
        "holistic_mode": holistic_mode,
        "holistic_samples": int(holistic_samples),
        "rank_target": "rank_score",
        "related_work": related_work,
    }
    if actual != FORECAST_K1:
        raise FixedEvalError(
            "fixed-set ICLR run must use --cheap --holistic-mode forecast "
            "--holistic-samples 1 --related-work off --coefficient-set apr-fusion-v1"
        )


def assert_holdout_file(path: Path, manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Recompute the private-holdout HMACs. Refuse a mismatch or a dev-set overlap."""
    payload = assert_manifest(manifest)
    try:
        operator = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixedEvalError(f"holdout operator file cannot be read: {exc}") from exc
    if not isinstance(operator, dict):
        raise FixedEvalError("holdout operator file must be an object")
    salt_hex = str(operator.get("salt_hex") or "")
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError as exc:
        raise FixedEvalError("holdout salt must be hex") from exc
    if len(salt) < 16:
        raise FixedEvalError("holdout salt must be at least 16 bytes")
    papers = operator.get("papers")
    if not isinstance(papers, list):
        raise FixedEvalError("holdout operator file needs a papers list")
    for index, paper in enumerate(papers):
        _check_paper(paper, where=f"holdout[{index}]")
    ids = [str(paper["forum_id"]) for paper in papers]
    if len(ids) != len(set(ids)):
        raise FixedEvalError("holdout forum ids must be unique")
    overlap = set(ids) & set(dev_ids(payload))
    if overlap:
        raise FixedEvalError(f"private holdout overlaps the dev set ({len(overlap)} papers)")
    counts = {name: 0 for name in TIER_NAMES}
    for paper in papers:
        counts[str(paper["tier"])] += 1
    if counts != {name: HOLDOUT_PER_TIER for name in TIER_NAMES}:
        raise FixedEvalError(f"private holdout tiers must be {HOLDOUT_PER_TIER} each, found {counts}")
    macs, list_mac = _hmac_ids(salt, ids)
    holdout = payload["holdout"]
    if macs != list(holdout["id_hmac_sha256"]) or list_mac != holdout["list_hmac_sha256"]:
        raise FixedEvalError("holdout operator file does not match the committed HMACs")
    return list(papers)


def _shuffle(papers: list[dict[str, Any]], *, seed: int, tier: str) -> list[dict[str, Any]]:
    ids = sorted(str(paper["forum_id"]) for paper in papers)
    rng = random.Random(f"fixed-eval-v1:{seed}:{tier}")
    rng.shuffle(ids)
    rank = {forum_id: index for index, forum_id in enumerate(ids)}
    return sorted(papers, key=lambda paper: rank[str(paper["forum_id"])])


def _take(
    ordered: list[dict[str, Any]],
    count: int,
    area_counts: dict[str, int],
    area_cap: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chosen: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for paper in ordered:
        area = str(paper.get("primary_area") or "unspecified")
        if len(chosen) < count and area_counts.get(area, 0) < area_cap:
            chosen.append(paper)
            area_counts[area] = area_counts.get(area, 0) + 1
        else:
            rest.append(paper)
    if len(chosen) < count:
        still: list[dict[str, Any]] = []
        for paper in rest:
            if len(chosen) < count:
                area = str(paper.get("primary_area") or "unspecified")
                chosen.append(paper)
                area_counts[area] = area_counts.get(area, 0) + 1
            else:
                still.append(paper)
        rest = still
    return chosen, rest


def select_fixed(
    papers: list[dict[str, Any]],
    *,
    seed: int = SELECTION_SEED,
    dev_n: int = DEV_PER_TIER,
    hold_n: int = HOLDOUT_PER_TIER,
    area_cap: int = AREA_CAP,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Equal-count dev and holdout draws. Input order does not matter.

    Each paper needs ``forum_id``, ``primary_area``, and ``mean`` or
    ``mean_rating``. Tiers come from :func:`tier_for`. Inside a tier the ids
    are sorted and shuffled with ``Random(f"fixed-eval-v1:{seed}:{tier}")``.
    The walk takes at most ``area_cap`` papers from one primary area, then
    relaxes that cap if the quota is still short. The dev draw comes first.
    The holdout draw continues through the same order and does not reuse a
    dev paper.
    """
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in TIER_NAMES}
    for paper in papers:
        raw = paper.get("mean_rating", paper.get("mean"))
        if raw is None:
            raise FixedEvalError("a candidate is missing its mean rating")
        buckets[tier_for(float(raw))].append(paper)
    dev: list[dict[str, Any]] = []
    hold: list[dict[str, Any]] = []
    for tier in TIER_NAMES:
        ordered = _shuffle(buckets[tier], seed=seed, tier=tier)
        counts: dict[str, int] = {}
        chosen, rest = _take(ordered, dev_n, counts, area_cap)
        held, _leftover = _take(rest, hold_n, counts, area_cap)
        dev.extend(chosen)
        hold.extend(held)
    return dev, hold


def holdout_record(salt: bytes, papers: list[dict[str, Any]]) -> dict[str, Any]:
    """Committed holdout block. Ids and the salt are not included."""
    macs, list_mac = _hmac_ids(salt, [str(paper["forum_id"]) for paper in papers])
    counts = {name: 0 for name in TIER_NAMES}
    for paper in papers:
        raw = paper.get("mean_rating", paper.get("mean"))
        counts[tier_for(float(raw))] += 1
    return {
        "hmac_alg": "HMAC-SHA256",
        "id_hmac_sha256": macs,
        "list_hmac_sha256": list_mac,
        "n": len(papers),
        "operator_file": "workdir/fixed-eval/private-holdout.json",
        "per_tier": counts,
        "role": "private",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check the fixed evaluation manifest")
    parser.add_argument("command", choices=("check", "ids", "holdout-check"))
    parser.add_argument("path", nargs="?")
    parser.add_argument("--write", help="gitignored path for the dev id list")
    args = parser.parse_args(argv)
    if args.command == "check":
        payload = assert_manifest()
        print(f"status={payload['status']} dev={len(dev_ids(payload))} digest={payload['content_sha256'][:12]}")
        return 0
    if args.command == "ids":
        ids = dev_ids()
        if args.write:
            from .public_calibration import assert_local_only

            destination = assert_local_only(Path(args.write))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {len(ids)} ids")
        else:
            print(f"n={len(ids)}")
        return 0
    if not args.path:
        raise FixedEvalError("holdout-check needs the operator file path")
    papers = assert_holdout_file(Path(args.path))
    print(f"holdout_ok n={len(papers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
