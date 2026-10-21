#!/usr/bin/env python3
"""Build a reproducible 100-paper public ICLR calibration sample.

The sample is stratified by the mean of public official ratings so the
ground truth covers clear rejects, borderline papers, and strong accepts,
and by primary area so one topic does not fill a stratum. Seed and quotas
live in ``autopaperreview.benchmark_select``.

OpenReview's direct notes route often answers with a browser check. This
script uses the public search route, which returns the same venue notes.
PDFs that the venue will not serve fall back to an arXiv file whose title
matches exactly. Manuscripts, forum ids, titles, and ratings are written
only under the cache directory (default ``workdir/calibration-bench/``).
Do not commit that directory.

Examples:

    PYTHONPATH=src python3 scripts/select_public_calibration.py --cache workdir/calibration-bench
    PYTHONPATH=src python3 scripts/select_public_calibration.py --cache workdir/calibration-bench --phase pool
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.benchmark_select import (  # noqa: E402
    SEED,
    Candidate,
    assign_split,
    criteria_public,
    select_benchmark,
    stratum_for,
)
from autopaperreview.public_calibration import (  # noqa: E402
    VENUE_LISTINGS,
    ChallengeRequired,
    ManifestPaper,
    PublicCalibrationError,
    assert_local_only,
    conference_group,
    content_value,
    decision_of,
    default_arxiv_finder,
    human_ratings,
    materialize_preloaded,
    submission_number,
)

USER_AGENT = "AutoPaperReview-calibration-eval"
API_ROOT = "https://api2.openreview.net"
PAGE_SIZE = 50
# Review a deterministic prefix of each listing, largest tail first.
# Oral and spotlight are small, so they are read in full.
REVIEW_LIMITS = {
    "oral": None,
    "spotlight": None,
    "rejected": 900,
    "poster": 400,
}
SURPLUS_FACTOR = 3
LISTING_PRIORITY = ("oral", "spotlight", "rejected", "poster")


def _listing_kind(label: str) -> str:
    lowered = label.lower()
    for kind in LISTING_PRIORITY:
        if kind in lowered:
            return kind
    raise PublicCalibrationError(f"listing has no known kind: {label}")


def _year_of(label: str) -> int:
    for year in (2024, 2025):
        if str(year) in label:
            return year
    raise PublicCalibrationError(f"listing has no year: {label}")


class SearchClient:
    """Public search client with retries. No credentials."""

    def __init__(self, *, timeout: float = 60, min_interval: float = 0.05) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self._last = 0.0

    def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        url = API_ROOT + path + "?" + query
        delay = 0.5
        last_error: Exception | None = None
        for _attempt in range(8):
            now = time.monotonic()
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read()
                last_error = exc
                if exc.code == 429 or exc.code >= 500:
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                if exc.code == 403 or b"ChallengeRequired" in body:
                    raise ChallengeRequired("OpenReview asked for a browser check on this route") from exc
                raise PublicCalibrationError(f"OpenReview HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                last_error = exc
                time.sleep(delay)
                delay = min(delay * 2, 20)
                continue
            if not isinstance(payload, dict):
                raise PublicCalibrationError("OpenReview notes payload was not an object")
            return payload
        raise PublicCalibrationError(f"OpenReview search failed after retries: {last_error}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_listing(client: SearchClient, spec: dict[str, str], *, resume: Path | None = None) -> list[dict[str, Any]]:
    """Page one public venue listing. Titles stay in the returned notes."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    if resume is not None and resume.is_file():
        for line in resume.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            note = json.loads(line)
            forum_id = str(note.get("forum") or note.get("id") or "")
            if forum_id and forum_id not in seen:
                seen.add(forum_id)
                found.append(note)
        offset = len(found)
    pages = 0
    while pages < 400:
        pages += 1
        data = client.get_json(
            "/notes/search",
            {
                "term": spec["term"],
                "group": spec["group"],
                "invitation": spec["invitation"],
                "limit": PAGE_SIZE,
                "offset": offset,
            },
        )
        notes = data.get("notes") or []
        if not notes:
            break
        fresh = 0
        page_notes = []
        for note in notes:
            forum_id = str(note.get("forum") or note.get("id") or "")
            if not forum_id or forum_id in seen:
                continue
            seen.add(forum_id)
            fresh += 1
            found.append(note)
            page_notes.append(note)
        if resume is not None and page_notes:
            resume.parent.mkdir(parents=True, exist_ok=True)
            with resume.open("a", encoding="utf-8") as handle:
                for note in page_notes:
                    handle.write(json.dumps(note, sort_keys=True) + "\n")
        if fresh == 0 or len(notes) < PAGE_SIZE:
            break
        offset += len(notes)
    return found


def stub_from_note(note: dict[str, Any], spec: dict[str, str]) -> dict[str, Any] | None:
    decision = decision_of(note)
    if decision not in {"accept", "reject"}:
        return None
    forum_id = str(note.get("forum") or note.get("id") or "")
    number = submission_number(note)
    title = str(content_value(note, "title") or "").strip()
    if not forum_id or number is None or not title:
        return None
    venueid = str(content_value(note, "venueid") or "")
    area = str(content_value(note, "primary_area") or "").strip() or "unspecified"
    return {
        "forum_id": forum_id,
        "number": number,
        "year": _year_of(spec["label"]),
        "listing": spec["label"],
        "kind": _listing_kind(spec["label"]),
        "decision": decision,
        "primary_area": " ".join(area.split()),
        "title": title,
        "group": conference_group(venueid) or spec["group"],
        "venueid": venueid,
    }


def load_stubs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    stubs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            stubs.append(json.loads(line))
    return stubs


def _listing_cache_path(cache: Path, label: str) -> Path:
    safe = label.replace(" ", "-").lower()
    return cache / "pool" / "listings" / f"{safe}.jsonl"


def collect_stubs(cache: Path, client: SearchClient) -> list[dict[str, Any]]:
    path = cache / "pool" / "stubs.jsonl"
    marker = cache / "pool" / "stubs.complete"
    existing = load_stubs(path)
    if existing and marker.is_file():
        print(f"stubs cached {len(existing)}", file=sys.stderr)
        return existing
    by_forum: dict[str, dict[str, Any]] = {}
    for spec in VENUE_LISTINGS:
        listing_path = _listing_cache_path(cache, spec["label"])
        if listing_path.is_file():
            stubs = load_stubs(listing_path)
            print(f"listed {spec['label']} cached {len(stubs)}", file=sys.stderr)
        else:
            raw_path = listing_path.with_suffix(".notes.jsonl")
            notes = collect_listing(SearchClient(min_interval=0.35), spec, resume=raw_path)
            stubs = []
            for note in notes:
                stub = stub_from_note(note, spec)
                if stub is not None:
                    stubs.append(stub)
            listing_path.parent.mkdir(parents=True, exist_ok=True)
            listing_path.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in stubs),
                encoding="utf-8",
            )
            print(f"listed {spec['label']} stubs {len(stubs)}", file=sys.stderr)
        for stub in stubs:
            current = by_forum.get(stub["forum_id"])
            if current is None or LISTING_PRIORITY.index(stub["kind"]) < LISTING_PRIORITY.index(current["kind"]):
                by_forum[stub["forum_id"]] = stub
    ordered = [by_forum[key] for key in sorted(by_forum)]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in ordered), encoding="utf-8")
    temporary.replace(path)
    marker.write_text("ok\n", encoding="utf-8")
    print(f"stubs written {len(ordered)}", file=sys.stderr)
    return ordered


def review_schedule(stubs: list[dict[str, Any]], *, seed: int) -> list[dict[str, Any]]:
    """Deterministic review order. Oral and spotlight come before sampled tails."""
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for stub in stubs:
        grouped.setdefault((stub["kind"], int(stub["year"])), []).append(stub)
    scheduled: list[dict[str, Any]] = []
    for kind in LISTING_PRIORITY:
        limit = REVIEW_LIMITS[kind]
        for year in (2024, 2025):
            group = sorted(grouped.get((kind, year), []), key=lambda item: item["forum_id"])
            rng = random.Random(f"{seed}:review:{kind}:{year}")
            rng.shuffle(group)
            if limit is not None:
                group = group[:limit]
            scheduled.extend(group)
    return scheduled


def _ratings_for(stub: dict[str, Any]) -> tuple[str, list[int], list[dict[str, Any]]]:
    client = SearchClient(min_interval=0.0)
    invitation = f"{stub['group']}/Submission{stub['number']}/-/Official_Review"
    notes: list[dict[str, Any]] = []
    for term in ("rating", "the"):
        data = client.get_json(
            "/notes/search",
            {"term": term, "invitation": invitation, "limit": 50},
        )
        for note in data.get("notes") or []:
            if note.get("forum") == stub["forum_id"]:
                notes.append(note)
        ratings, _labels = human_ratings(notes)
        if len(ratings) >= 2:
            break
    ratings, _labels = human_ratings(notes)
    return stub["forum_id"], ratings, notes


def score_schedule(
    cache: Path,
    schedule: list[dict[str, Any]],
    *,
    workers: int,
) -> dict[str, dict[str, Any]]:
    """Fetch official ratings until every stratum has a PDF-failure surplus."""
    scored_path = cache / "pool" / "scored.json"
    notes_dir = cache / "pool" / "reviews"
    notes_dir.mkdir(parents=True, exist_ok=True)
    scored: dict[str, dict[str, Any]] = {}
    if scored_path.is_file():
        scored = _read_json(scored_path)
    pending = [stub for stub in schedule if stub["forum_id"] not in scored]
    quotas = {name: quota for name, _low, _high, quota in _stratum_table()}
    targets = {name: quota * SURPLUS_FACTOR for name, quota in quotas.items()}

    def _filled(records: dict[str, dict[str, Any]]) -> bool:
        counts = {name: 0 for name in targets}
        for record in records.values():
            ratings = tuple(int(item) for item in record.get("ratings") or [])
            if len(ratings) < 2:
                continue
            name = stratum_for(sum(ratings) / len(ratings))
            if name in counts:
                counts[name] += 1
        return all(counts[name] >= targets[name] for name in targets)

    if pending and not _filled(scored):
        batch: list[dict[str, Any]] = []
        for stub in pending:
            batch.append(stub)
            if len(batch) < max(workers * 4, 16):
                continue
            _score_batch(batch, scored, notes_dir, workers)
            _write_json(scored_path, scored)
            _print_strata(scored)
            batch = []
            if _filled(scored):
                break
        if batch and not _filled(scored):
            _score_batch(batch, scored, notes_dir, workers)
            _write_json(scored_path, scored)
            _print_strata(scored)
    else:
        _print_strata(scored)
    return scored


def _stratum_table():
    from autopaperreview.benchmark_select import STRATA

    return STRATA


def _score_batch(
    batch: list[dict[str, Any]],
    scored: dict[str, dict[str, Any]],
    notes_dir: Path,
    workers: int,
) -> None:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_ratings_for, stub) for stub in batch]
        stubs_by_id = {stub["forum_id"]: stub for stub in batch}
        for future in as_completed(futures):
            try:
                forum_id, ratings, notes = future.result()
            except (PublicCalibrationError, ChallengeRequired, urllib.error.URLError, OSError) as exc:
                print(f"rating fetch failed {exc.__class__.__name__}", file=sys.stderr)
                continue
            stub = stubs_by_id[forum_id]
            scored[forum_id] = {
                "forum_id": forum_id,
                "year": stub["year"],
                "listing": stub["listing"],
                "decision": stub["decision"],
                "primary_area": stub["primary_area"],
                "ratings": ratings,
                "title": stub["title"],
                "number": stub["number"],
                "group": stub["group"],
            }
            _write_json(notes_dir / f"{forum_id}.json", notes)


def _print_strata(scored: dict[str, dict[str, Any]]) -> None:
    counts = {name: 0 for name, _low, _high, _quota in _stratum_table()}
    kept = 0
    for record in scored.values():
        ratings = [int(item) for item in record.get("ratings") or []]
        if len(ratings) < 2:
            continue
        kept += 1
        name = stratum_for(sum(ratings) / len(ratings))
        if name in counts:
            counts[name] += 1
    print(f"scored_with_two_ratings {kept} strata {counts}", file=sys.stderr)


def candidates_from_scored(scored: dict[str, dict[str, Any]]) -> list[Candidate]:
    found = []
    for record in scored.values():
        ratings = tuple(int(item) for item in record.get("ratings") or [])
        if len(ratings) < 2:
            continue
        found.append(
            Candidate(
                forum_id=str(record["forum_id"]),
                year=int(record["year"]),
                listing=str(record["listing"]),
                decision=str(record["decision"]),
                primary_area=str(record["primary_area"]),
                ratings=ratings,
            )
        )
    return found


class _PdfBlocked:
    def get_bytes(self, url: str) -> bytes:
        raise ChallengeRequired(url)


def _arxiv_finder(title: str) -> tuple[str, bytes] | None:
    last: Exception | None = None
    for attempt in range(4):
        try:
            return default_arxiv_finder(title)
        except (PublicCalibrationError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    if last is not None:
        raise PublicCalibrationError(f"arXiv lookup failed: {last}") from last
    return None


def _strata_filled(scored: dict[str, dict[str, Any]]) -> bool:
    targets = {name: quota * SURPLUS_FACTOR for name, _low, _high, quota in _stratum_table()}
    counts = {name: 0 for name in targets}
    for record in scored.values():
        ratings = [int(item) for item in record.get("ratings") or []]
        if len(ratings) < 2:
            continue
        name = stratum_for(sum(ratings) / len(ratings))
        if name in counts:
            counts[name] += 1
    return all(counts[name] >= targets[name] for name in targets)


def materialize(cache: Path, scored: dict[str, dict[str, Any]], *, seed: int) -> dict[str, Any]:
    """Walk the stratified order and keep a paper when its text extracts."""
    selection = select_benchmark(candidates_from_scored(scored), seed=seed)
    order: list[Candidate] = []
    for name, _low, _high, _quota in _stratum_table():
        order.extend(selection.by_stratum[name])
        order.extend(selection.alternate_by_stratum[name])
    quotas = {name: quota for name, _low, _high, quota in _stratum_table()}
    kept: dict[str, list[Candidate]] = {name: [] for name in quotas}
    failures = 0
    reviews_dir = cache / "pool" / "reviews"
    for candidate in order:
        name = stratum_for(candidate.mean)
        if name is None or len(kept[name]) >= quotas[name]:
            continue
        record = scored[candidate.forum_id]
        destination = cache / "papers" / candidate.forum_id / "status.json"
        if destination.is_file():
            status = _read_json(destination)
            if status.get("state") == "fetched":
                kept[name].append(candidate)
                continue
            if status.get("state") == "error":
                failures += 1
                continue
        reviews = _read_json(reviews_dir / f"{candidate.forum_id}.json")
        submission = {
            "id": candidate.forum_id,
            "forum": candidate.forum_id,
            "number": record["number"],
            "content": {
                "title": {"value": record["title"]},
                "venue": {"value": candidate.listing},
                "venueid": {"value": record["group"]},
                "pdf": {"value": ""},
            },
        }
        try:
            status = materialize_preloaded(
                cache,
                ManifestPaper(forum_id=candidate.forum_id, decision=candidate.decision, listing=candidate.listing),
                submission,
                reviews,
                pdf_fallback="arxiv",
                client=_PdfBlocked(),
                arxiv_finder=_arxiv_finder,
            )
        except (PublicCalibrationError, OSError, urllib.error.URLError) as exc:
            status = {"forum_id": candidate.forum_id, "state": "error", "error": str(exc)}
            _write_json(cache / "papers" / candidate.forum_id / "status.json", status)
        if status.get("state") == "fetched":
            kept[name].append(candidate)
            print(
                f"fetched stratum {name} {len(kept[name])}/{quotas[name]} chars {status.get('text_chars')}",
                file=sys.stderr,
            )
        else:
            failures += 1
            print(f"skip stratum {name} failures {failures}", file=sys.stderr)
        if all(len(kept[item]) >= quotas[item] for item in quotas):
            break
    chosen = [item for name in quotas for item in kept[name]]
    papers = [
        {"forum_id": item.forum_id, "decision": item.decision, "listing": item.listing}
        for item in chosen
    ]
    manifest = {"papers": papers}
    _write_json(cache / "manifest.json", manifest)
    tune, holdout = assign_split(chosen, seed=seed)
    _write_json(
        cache / "split.json",
        {
            "seed": seed,
            "tune_fraction": 0.6,
            "tune": [item.forum_id for item in tune],
            "holdout": [item.forum_id for item in holdout],
            "strata": {name: [item.forum_id for item in kept[name]] for name in quotas},
        },
    )
    _write_json(
        cache / "selection_criteria.json",
        {
            **criteria_public(),
            "materialized": {name: len(kept[name]) for name in quotas},
            "pdf_failures": failures,
            "shortfalls_before_pdf": selection.shortfalls,
        },
    )
    print(
        f"materialized {len(chosen)} tune {len(tune)} holdout {len(holdout)} failures {failures}",
        file=sys.stderr,
    )
    return {"n": len(chosen), "tune": len(tune), "holdout": len(holdout), "failures": failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select a reproducible public calibration sample")
    parser.add_argument("--cache", default="workdir/calibration-bench")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--phase", choices=("all", "pool", "materialize"), default="all")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    cache = assert_local_only(Path(args.cache))
    cache.mkdir(parents=True, exist_ok=True)
    _write_json(cache / "criteria.json", criteria_public())
    if args.phase in {"all", "pool"}:
        stubs = collect_stubs(cache, SearchClient())
        schedule = review_schedule(stubs, seed=args.seed)
        _write_json(
            cache / "pool" / "schedule_ids.json",
            [item["forum_id"] for item in schedule],
        )
        scored = score_schedule(cache, schedule, workers=args.workers)
        if not _strata_filled(scored):
            seen = {item["forum_id"] for item in schedule}
            extra = [
                stub
                for stub in stubs
                if stub["forum_id"] not in seen and stub["kind"] in {"rejected", "poster"}
            ]
            rng = random.Random(f"{args.seed}:review-extra")
            rng.shuffle(extra)
            print(f"extending review sample by {len(extra)}", file=sys.stderr)
            score_schedule(cache, extra, workers=args.workers)
    if args.phase in {"all", "materialize"}:
        scored = _read_json(cache / "pool" / "scored.json")
        materialize(cache, scored, seed=args.seed)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PublicCalibrationError, ChallengeRequired) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
