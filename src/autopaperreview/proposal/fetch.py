"""Verified public October/November OpenReview snapshots, plus an API v2 intake.

The live client is the shared OpenReview client: it waits between calls. HTTP 403
stops this intake. It does not fall through to search.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

from ..hashing import sha256_file
from ..public_calibration import ChallengeRequired, HttpOpenReviewClient, content_value
from .data import iclr_case

ROOT = "https://github.com/ranpox/iclr2024-openreview-submissions/releases/download/"
SNAPSHOTS = (
    (
        ROOT + "v0.1/raw_paperlist.json",
        "b436c8949b9d74b2ab1dbd8332d6ada57b3db064e031c1a4e4203ffc4ef60527",
    ),
    (
        ROOT + "v0.2/raw_paper_reviews.jsonl",
        "9cd41d2f1e741618525d28fbdc3d2d442d829f90cfc265f9a32291239fe8957d",
    ),
)


def download(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for url, expected in SNAPSHOTS:
        path = folder / url.rsplit("/", 1)[1]
        if not path.exists() or sha256_file(path) != expected:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as f:
                temp = Path(f.name)
            try:
                with (
                    urllib.request.urlopen(url, timeout=90) as response,
                    temp.open("wb") as out,
                ):
                    shutil.copyfileobj(response, out)
                if sha256_file(temp) != expected:
                    raise ValueError("public snapshot failed SHA-256 verification")
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        paths.append(path)
    return paths


def _notes(client, path, params):
    try:
        payload = client.get_json(path, params)
    except ChallengeRequired as exc:
        raise ChallengeRequired(
            "OpenReview returned HTTP 403 or a browser check. "
            "Proposal intake stopped. No further requests."
        ) from exc
    notes = payload.get("notes") if isinstance(payload, dict) else None
    if not isinstance(notes, list):
        raise ValueError("OpenReview notes payload did not contain a notes array")
    return notes


def live_cases(year, pages, areas, allow_late):
    client = HttpOpenReviewClient(sleeper=time.sleep)
    group = f"ICLR.cc/{year}/Conference"
    invitation = group + "/-/Submission"
    seen = set()
    for page in range(pages):
        params = {"invitation": invitation, "limit": 50, "offset": 50 * page}
        notes = _notes(client, "/notes", params)
        for sub in notes:
            if sub["id"] in seen or (
                areas and content_value(sub, "primary_area") not in areas
            ):
                continue
            seen.add(sub["id"])
            replies = _notes(client, "/notes", {"forum": sub["id"], "limit": 1000})
            case = iclr_case(sub, replies, group, allow_late=allow_late)
            if case:
                yield case
        if len(notes) < 50:
            break
