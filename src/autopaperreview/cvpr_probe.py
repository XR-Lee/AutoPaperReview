"""Contamination probes for the CVPR tier loop.

P-A shows an abstract, P-T shows a title, and P-D asks which of four
passages is verbatim. A batch mixes targets with negative controls,
synthetic decoys, and other-year items. Scores are title token-F1,
surname overlap, venue/year, DE-COP, and a tier-QA map. True program
tiers are not read here. The report joins them later.

arXiv metadata is fetched only when a caller supplies a network getter
or runs ``cvpr-tier controls``. Tests pass a fixture feed.
"""

from __future__ import annotations

import json
import random
import re
import xml.etree.ElementTree as ElementTree
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .cvpr_anonymize import extract_meta
from .hashing import sha256_bytes

TEMPLATES = ("P-A", "P-T", "P-D")
TITLE_HIT_F1 = 0.8
DECOP_CHANCE = 0.25
DECOP_RECOGNISED = 3
TIER_QA = {
    "oral": 3,
    "highlight": 2,
    "spotlight": 2,
    "poster": 1,
    "unknown": 2,
    "reject": 0,
}
_STOP = frozenset(
    """
    a an the of and or to in on for with from by at as is are was were be been being
    this that these those it its their his her them they we our you your not into over
    under than then so if but about across after before during without within onto off
    out up down can may via using use used
    """.split()
)
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_TOKEN = re.compile(r"[a-z0-9]+")
_HANDOFF = re.compile(r"^[0-9a-f]{12}$")
ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
DEFAULT_CONTROLS_SINCE = "2026-07-15"
DEFAULT_CONTROLS_CATEGORY = "cs.CV"


class ProbeError(ValueError):
    """A probe batch, feed, or answer cannot be scored."""


def tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN.findall(text.lower()) if token not in _STOP and len(token) > 1]


def token_f1(predicted: str, gold: str) -> float:
    """Multiset token F1 after stopword removal."""
    pred = Counter(tokenize(predicted))
    truth = Counter(tokenize(gold))
    if not pred and not truth:
        return 1.0
    if not pred or not truth:
        return 0.0
    overlap = sum((pred & truth).values())
    precision = overlap / sum(pred.values())
    recall = overlap / sum(truth.values())
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def surnames_of(authors: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for author in authors:
        parts = re.findall(r"[A-Za-z][A-Za-z'’.-]*", author)
        if not parts:
            continue
        last = parts[-1].strip(".-'’")
        key = last.lower()
        if len(last) < 2 or key in seen:
            continue
        seen.add(key)
        found.append(last)
    return found


def surname_match(predicted: str | list[str], gold: list[str]) -> dict[str, Any]:
    """Hit when the first surname, the last surname, or at least two surnames match."""
    text = predicted if isinstance(predicted, str) else " ".join(predicted)
    matched = [surname for surname in gold if re.search(rf"(?i)\b{re.escape(surname)}\b", text)]
    first = gold[0] if gold else None
    last = gold[-1] if gold else None
    hit = bool(matched) and (first in matched or last in matched or len(matched) >= 2)
    score = 0.0 if not gold else len(matched) / len(gold)
    return {"hit": hit, "matched": matched, "score": score}


def venue_year_score(predicted_venue: str, predicted_year: object, gold_venue: str, gold_year: object) -> dict[str, Any]:
    venue_hit = _venue_hit(predicted_venue, gold_venue)
    year_hit = _year_hit(predicted_year, gold_year)
    parts = [1.0 if venue_hit else 0.0, 1.0 if year_hit else 0.0]
    return {"venue_hit": venue_hit, "year_hit": year_hit, "score": sum(parts) / 2.0}


def tier_qa(label: object) -> int | None:
    if label is None:
        return None
    return TIER_QA.get(str(label).strip().lower())


def make_decop_questions(verbatim: str, paraphrases: list[str], *, seed: int) -> list[dict[str, Any]]:
    """Four questions. Each question has the verbatim passage and three paraphrases."""
    if not verbatim.strip():
        raise ProbeError("P-D needs a verbatim passage")
    if len(paraphrases) != 3 or any(not str(item).strip() for item in paraphrases):
        raise ProbeError("P-D needs three paraphrases")
    questions: list[dict[str, Any]] = []
    for index in range(4):
        rng = random.Random(f"{seed}:decop:{index}")
        options = [verbatim, *paraphrases]
        rng.shuffle(options)
        answer = "ABCD"[options.index(verbatim)]
        questions.append(
            {
                "options": {letter: options[offset] for offset, letter in enumerate("ABCD")},
                "answer": answer,
            }
        )
    return questions


def decop_accuracy(choices: list[str], answers: list[str]) -> dict[str, Any]:
    if not answers:
        return {"accuracy": None, "correct": 0, "total": 0}
    correct = 0
    for choice, answer in zip(choices, answers, strict=False):
        if str(choice).strip().upper()[:1] == str(answer).strip().upper()[:1]:
            correct += 1
    total = len(answers)
    return {"accuracy": correct / total, "correct": correct, "total": total}


def score_item(template: str, parsed: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    """Feature scores for one item. The program tier is not an input."""
    title = token_f1(str(parsed.get("title") or ""), str(gold.get("title") or ""))
    authors = surname_match(parsed.get("authors") or "", list(gold.get("surnames") or []))
    venue = venue_year_score(
        str(parsed.get("venue") or ""),
        parsed.get("year"),
        str(gold.get("venue") or ""),
        gold.get("year"),
    )
    answers = list(gold.get("decop_answers") or [])
    choices = _choice_list(parsed.get("choices"))
    decop = decop_accuracy(choices, answers) if template == "P-D" else {"accuracy": None, "correct": 0, "total": 0}
    return {
        "author_hit": bool(authors["hit"]) if template in {"P-A", "P-T"} else False,
        "author_score": authors["score"] if template in {"P-A", "P-T"} else None,
        "decop_accuracy": decop["accuracy"],
        "decop_correct": decop["correct"] if template == "P-D" else None,
        "decop_total": decop["total"] if template == "P-D" else None,
        "template": template,
        "tier_qa": tier_qa(parsed.get("tier")),
        "title_f1": title if template == "P-A" else None,
        "title_hit": bool(template == "P-A" and title >= TITLE_HIT_F1),
        "venue_hit": bool(venue["venue_hit"]) if template in {"P-A", "P-T"} else False,
        "venue_year_score": venue["score"] if template in {"P-A", "P-T"} else None,
        "year_hit": bool(venue["year_hit"]) if template in {"P-A", "P-T"} else False,
    }


def recognise_paper(features: list[dict[str, Any]]) -> dict[str, Any]:
    """R is a P-A title hit, a P-A or P-T author hit, or at least 3 of 4 DE-COP items."""
    title_hit = any(row.get("title_hit") for row in features if row.get("template") == "P-A")
    author_hit = any(row.get("author_hit") for row in features if row.get("template") in {"P-A", "P-T"})
    decop = next((row for row in features if row.get("template") == "P-D" and row.get("decop_total")), None)
    decop_hit = bool(
        decop
        and int(decop.get("decop_total") or 0) >= 4
        and int(decop.get("decop_correct") or 0) >= DECOP_RECOGNISED
    )
    title_f1 = next((row.get("title_f1") for row in features if row.get("template") == "P-A"), None)
    author_score = max(
        (float(row["author_score"]) for row in features if isinstance(row.get("author_score"), (int, float))),
        default=None,
    )
    venue_score = max(
        (float(row["venue_year_score"]) for row in features if isinstance(row.get("venue_year_score"), (int, float))),
        default=None,
    )
    excess = None
    if decop and isinstance(decop.get("decop_accuracy"), (int, float)):
        excess = max(0.0, float(decop["decop_accuracy"]) - DECOP_CHANCE)
    parts = [value for value in (title_f1, author_score, venue_score, excess) if value is not None]
    familiarity = None if not parts else sum(parts) / len(parts)
    return {
        "author_hit": author_hit,
        "decop_hit": decop_hit,
        "familiarity": familiarity,
        "recognised": bool(title_hit or author_hit or decop_hit),
        "title_hit": title_hit,
    }


def extract_abstract(text: str) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.fullmatch(r"(?i)abstract[:.]?", line.strip()):
            start = index + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        if re.fullmatch(r"(?i)(references|acknowledgements|acknowledgment|[0-9.]*\s*introduction)", line.strip()):
            break
        body.append(line)
    paragraph = "\n".join(body).strip()
    return paragraph.split("\n\n")[0].strip()


def target_from_text(paper_id: str, year: int, text: str, passages: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Operator-side record. Returns None when the text has no abstract."""
    abstract = extract_abstract(text)
    meta = extract_meta(text)
    title = str(meta.get("title") or "").strip()
    if not abstract or not title:
        return None
    record: dict[str, Any] = {
        "abstract": abstract,
        "authors": list(meta.get("authors") or []),
        "item_id": paper_id,
        "kind": "target",
        "paper_id": paper_id,
        "surnames": list(meta.get("surnames") or []),
        "title": title,
        "venue": "CVPR",
        "year": year,
    }
    if passages:
        record["verbatim"] = str(passages.get("verbatim") or "")
        record["paraphrases"] = [str(item) for item in passages.get("paraphrases") or []]
    return record


def synthetic_decoys(count: int, *, seed: int) -> list[dict[str, Any]]:
    """Offline stand-ins. No network and no published abstract."""
    if count < 0:
        raise ProbeError("decoy count must be non-negative")
    rows: list[dict[str, Any]] = []
    for index in range(count):
        rng = random.Random(f"{seed}:decoy:{index}")
        given = rng.choice(("Nova", "Ivo", "Lumen", "Pia"))
        family = rng.choice(("Quilt", "Raster", "Mottle", "Cinder"))
        other = "Tess Binder" if family != "Binder" else "Tess Quilt"
        title = f"Synthetic lattice note {index} on cell crossings"
        rows.append(
            {
                "abstract": (
                    f"This synthetic stand-in {index} counts lattice cells in a fixed order "
                    "and is not a conference paper."
                ),
                "authors": [f"{given} {family}", other],
                "item_id": f"decoy-{index:02d}",
                "kind": "decoy",
                "paper_id": f"decoy-{index:02d}",
                "paraphrases": [
                    f"A stand-in note {index} tallies cells without leaving the grid.",
                    f"Note {index} only rehearses a cell count.",
                    f"The grid exercise {index} stops after the count.",
                ],
                "surnames": surnames_of([f"{given} {family}", other]),
                "title": title,
                "venue": "Synthetic Workshop",
                "verbatim": f"Synthetic stand-in {index} crosses eight cells.",
                "year": 2099,
            }
        )
    return rows


def build_arxiv_query(*, since: str = DEFAULT_CONTROLS_SINCE, category: str = DEFAULT_CONTROLS_CATEGORY, limit: int = 20) -> str:
    """Atom query for a category whose first version is on or after ``since``.

    The date window is submittedDate, which arXiv documents as the first-version time.
    """
    start = _parse_day(since)
    if limit < 1 or limit > 200:
        raise ProbeError("arXiv limit must be from 1 to 200")
    if not re.fullmatch(r"[A-Za-z0-9.+\-]+", category):
        raise ProbeError("arXiv category is not a single category id")
    window = f"[{start.strftime('%Y%m%d')}0000+TO+209912312359]"
    query = f"cat:{category}+AND+submittedDate:{window}"
    return (
        f"{ARXIV_ENDPOINT}?search_query={query}&start=0&max_results={limit}"
        "&sortBy=submittedDate&sortOrder=ascending"
    )


def parse_arxiv_atom(payload: str, *, since: str = DEFAULT_CONTROLS_SINCE, category: str = DEFAULT_CONTROLS_CATEGORY) -> list[dict[str, Any]]:
    """Parse an Atom feed. Entries before ``since`` or outside the category are dropped."""
    start = _parse_day(since)
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ProbeError("arXiv feed is not Atom XML") from exc
    rows: list[dict[str, Any]] = []
    for entry in root.findall(f"{_ATOM}entry"):
        published = _text(entry, "published")
        if not published:
            continue
        try:
            published_day = date.fromisoformat(published[:10])
        except ValueError:
            continue
        if published_day < start:
            continue
        primary = entry.find(f"{_ARXIV}primary_category")
        term = primary.attrib.get("term", "") if primary is not None else ""
        if term != category:
            continue
        identifier = _text(entry, "id")
        tail = identifier.rstrip("/").split("/")[-1]
        tail = re.sub(r"v\d+$", "", tail)
        authors = [_text(author, "name") for author in entry.findall(f"{_ATOM}author")]
        authors = [author for author in authors if author]
        title = " ".join(_text(entry, "title").split())
        abstract = " ".join(_text(entry, "summary").split())
        if not title or not abstract or not tail:
            continue
        rows.append(
            {
                "abstract": abstract,
                "authors": authors,
                "item_id": f"control-{tail}",
                "kind": "control",
                "paper_id": f"control-{tail}",
                "surnames": surnames_of(authors),
                "title": title,
                "venue": "arXiv",
                "year": published_day.year,
            }
        )
    return rows


def fetch_negative_controls(
    *,
    getter: Callable[[str], bytes] | None = None,
    since: str = DEFAULT_CONTROLS_SINCE,
    category: str = DEFAULT_CONTROLS_CATEGORY,
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch cs.CV metadata in a date window. Pass ``getter`` in tests. There is no default call in CI."""
    url = build_arxiv_query(since=since, category=category, limit=limit)
    if getter is None:
        getter = _network_get
    raw = getter(url)
    if isinstance(raw, str):
        text = raw
    else:
        text = raw.decode("utf-8", "replace")
    papers = parse_arxiv_atom(text, since=since, category=category)
    return {"category": category, "papers": papers, "since": since, "source": "arxiv", "url": url}


def assemble_batch(
    targets: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    decoys: list[dict[str, Any]],
    *,
    size: int = 10,
    seed: int = 0,
    primary_year: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Mix kinds, then shuffle. The prompt order does not encode the kind."""
    if size < 1:
        raise ProbeError("batch size must be positive")
    year_counts = Counter(item.get("year") for item in targets if item.get("year") is not None)
    if primary_year is None and year_counts:
        primary_year = int(year_counts.most_common(1)[0][0])
    buckets: dict[str, list[dict[str, Any]]] = {"target": [], "control": [], "decoy": [], "other_year": []}
    for item in targets:
        copy = dict(item)
        year = copy.get("year")
        if primary_year is not None and year not in (None, primary_year):
            copy["kind"] = "other_year"
            buckets["other_year"].append(copy)
        else:
            copy["kind"] = copy.get("kind") or "target"
            buckets["target"].append(copy)
    for item in controls:
        copy = dict(item)
        copy["kind"] = "control"
        buckets["control"].append(copy)
    for item in decoys:
        copy = dict(item)
        copy["kind"] = "decoy"
        buckets["decoy"].append(copy)
    rng = random.Random(seed)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    selected: list[dict[str, Any]] = []
    order = ("target", "control", "decoy", "other_year")
    while len(selected) < size and any(buckets[name] for name in order):
        for name in order:
            if buckets[name] and len(selected) < size:
                selected.append(buckets[name].pop(0))
    omitted = [str(item.get("item_id")) for name in order for item in buckets[name]]
    rng.shuffle(selected)
    return selected, omitted


def render_batch_prompt(template: str, items: list[dict[str, Any]]) -> str:
    """One prompt for a shuffled batch. Kinds, catalog ids, and answer letters stay out."""
    if template not in TEMPLATES:
        raise ProbeError(f"template must be one of {', '.join(TEMPLATES)}")
    blocks = [_template_instructions(template), ""]
    for item in items:
        handoff_id = str(item["handoff_id"])
        if not _HANDOFF.fullmatch(handoff_id):
            raise ProbeError("probe handoff id must be 12 hex characters")
        blocks.append(f"handoff_id: {handoff_id}")
        blocks.append(_item_body(template, item))
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def parse_probe_response(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        start = stripped.find("[")
        end = stripped.rfind("]")
    if start < 0 or end <= start:
        raise ProbeError("probe answer is not JSON")
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProbeError("probe answer is not JSON") from exc
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload["items"]
    elif isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = [payload]
    else:
        raise ProbeError("probe answer has no items")
    parsed: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        handoff_id = str(item.get("handoff_id") or "").strip().lower()
        parsed.append(
            {
                "authors": _authors(item.get("authors")),
                "choices": _choice_list(item.get("choices")),
                "handoff_id": handoff_id,
                "tier": str(item.get("tier") or "").strip().lower(),
                "title": str(item.get("title") or "").strip(),
                "venue": str(item.get("venue") or "").strip(),
                "year": _optional_year(item.get("year")),
            }
        )
    return parsed


def load_probe_summary(material: Path) -> dict[str, Any]:
    """Join probe scores to paper ids. Program tiers are not in these files."""
    probes = material / "probes"
    if not probes.is_dir():
        return {"models": {}}
    models: dict[str, Any] = {}
    for predictions_path in sorted(probes.rglob("predictions.json")):
        predictions = json.loads(predictions_path.read_text(encoding="utf-8"))
        model_id = str(predictions.get("model_id") or predictions_path.parent.name)
        variant = predictions.get("variant")
        key_name = _key_name(model_id, variant)
        keys_path = material / "probe_keys" / f"{key_name}.json"
        if not keys_path.is_file():
            continue
        keys = json.loads(keys_path.read_text(encoding="utf-8"))
        by_handoff = {str(item["handoff_id"]): item for item in keys.get("items") or []}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        kinds: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in predictions.get("items") or []:
            gold = by_handoff.get(str(item.get("handoff_id")))
            if gold is None:
                continue
            feature = dict(item.get("scores") or {})
            feature["template"] = item.get("template")
            paper_id = str(gold.get("paper_id") or "")
            kind = str(gold.get("kind") or "")
            grouped[paper_id].append(feature)
            kinds[kind].append({"paper_id": paper_id, **feature})
        papers = {}
        for paper_id, features in grouped.items():
            if paper_id.startswith("decoy-") or paper_id.startswith("control-"):
                continue
            papers[paper_id] = recognise_paper(features)
        kind_summary = {}
        for kind, rows in kinds.items():
            flags = []
            by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                by_paper[row["paper_id"]].append(row)
            for features in by_paper.values():
                flags.append(recognise_paper(features))
            recognised = sum(1 for flag in flags if flag["recognised"])
            familiarities = [flag["familiarity"] for flag in flags if isinstance(flag["familiarity"], (int, float))]
            kind_summary[kind] = {
                "mean_familiarity": None if not familiarities else sum(familiarities) / len(familiarities),
                "n": len(flags),
                "recognised": recognised,
            }
        run_key = model_id if not variant else f"{model_id}/{variant}"
        models[run_key] = {"kinds": kind_summary, "model_id": model_id, "papers": papers, "variant": variant}
    return {"models": models}


def run_probe(
    cache: Path,
    *,
    model_id: str,
    backend: str,
    command_template: str | None = None,
    command_timeout: float = 900.0,
    templates: tuple[str, ...] = TEMPLATES,
    batch_size: int = 10,
    seed: int = 0,
    decoy_count: int = 3,
    controls_path: Path | None = None,
    passages_path: Path | None = None,
    set_id: str | None = None,
    variant: str | None = None,
    handoff_dir: Path | None = None,
    custody: Path | None = None,
) -> dict[str, Any]:
    """Run probe batches. Does not open heldout tiers, catalog.json, or custody."""
    from .cvpr_handoff import (
        HandoffError,
        assert_model_visible,
        export_replay_packet,
        handoff_id_for,
        import_replay_answer,
        load_canaries,
        load_or_create_salt,
        run_isolated_command,
    )
    from .cvpr_tier import (
        CvprTierError,
        _append_jsonl,
        _cache,
        _handoff_root,
        _material_root,
        _remember_custody,
        _write_json,
        parse_model_id,
        read_judgment_text,
        render_command,
    )

    if backend == "adapter":
        raise ProbeError(
            "Models run through the local CLI command backend. "
            "The adapter backend is not used and no API key is read."
        )
    if backend not in {"command", "replay"}:
        raise ProbeError("probe backend must be command or replay")
    for template in templates:
        if template not in TEMPLATES:
            raise ProbeError(f"unknown template {template}")
    root = _cache(cache)
    _remember_custody(root, None if custody is None else Path(custody))
    label = parse_model_id(model_id)
    material = _material_root(root, set_id)
    manifest_path = material / "sample_manifest.json"
    manifest = json.loads(read_judgment_text(root, manifest_path))
    passages = _load_passages(Path(passages_path) if passages_path else None)
    controls = _load_control_file(Path(controls_path) if controls_path else None)
    targets: list[dict[str, Any]] = []
    skipped: list[str] = []
    for paper in manifest.get("papers") or []:
        paper_id = str(paper["paper_id"])
        raw_path = material / "raw" / f"{paper_id}.txt"
        if not raw_path.is_file():
            skipped.append(paper_id)
            continue
        record = target_from_text(
            paper_id,
            int(paper["year"]),
            read_judgment_text(root, raw_path),
            passages.get(paper_id),
        )
        if record is None:
            skipped.append(paper_id)
            continue
        targets.append(record)
    decoys = synthetic_decoys(decoy_count, seed=seed)
    if not targets and not controls and not decoys:
        raise ProbeError("probe batch is empty")
    salt = load_or_create_salt(root)
    destination = material / "probes" / label
    if variant:
        destination = destination / variant
    destination.mkdir(parents=True, exist_ok=True)
    key_items: list[dict[str, Any]] = []
    prediction_items: list[dict[str, Any]] = []
    omitted_ids: dict[str, list[str]] = {}
    errors: list[dict[str, str]] = []
    calls = 0
    tokens = 0
    handoff_root = None
    if backend == "replay":
        try:
            assert_model_visible([label], what="replay directory")
            handoff_root = _handoff_root(root, None if handoff_dir is None else Path(handoff_dir)) / label / "probes"
        except HandoffError as exc:
            raise ProbeError(str(exc)) from exc
        handoff_root.mkdir(parents=True, exist_ok=True)
    failure_log = destination / "failures.jsonl"
    for template in templates:
        selected, omitted = assemble_batch(
            targets,
            controls,
            decoys,
            size=batch_size,
            seed=seed + _template_seed(template),
        )
        omitted_ids[template] = omitted
        batch_items: list[dict[str, Any]] = []
        for offset, item in enumerate(selected):
            source = f"probe:{template}:{item['item_id']}:{offset}"
            handoff_id = handoff_id_for(salt, source)
            prepared = dict(item)
            prepared["handoff_id"] = handoff_id
            questions: list[dict[str, Any]] = []
            if template == "P-D" and prepared.get("verbatim") and prepared.get("paraphrases"):
                questions = make_decop_questions(
                    str(prepared["verbatim"]),
                    list(prepared["paraphrases"]),
                    seed=seed + offset,
                )
                prepared["questions"] = questions
            elif template == "P-D":
                continue
            batch_items.append(prepared)
            key_items.append(_key_record(prepared, template, questions, omitted=item["item_id"] in omitted))
        if not batch_items:
            continue
        prompt = render_batch_prompt(template, batch_items)
        for item in batch_items:
            forbidden = str(item.get("paper_id") or "")
            if forbidden.startswith(("decoy-", "control-")):
                continue
            if forbidden and re.search(
                rf"(?i)(?<![A-Za-z0-9_]){re.escape(forbidden)}(?![A-Za-z0-9_])",
                prompt,
            ):
                raise ProbeError("probe prompt contains a catalog id")
        digest = sha256_bytes(prompt.encode("utf-8"))
        batch_id = handoff_id_for(salt, f"batch:{template}:{digest}")
        (destination / "prompts").mkdir(parents=True, exist_ok=True)
        (destination / "prompts" / f"{template}-{batch_id}.txt").write_text(prompt, encoding="utf-8")
        try:
            text, usage = _invoke(
                backend,
                template=command_template or "",
                prompt=prompt,
                handoff_id=batch_id,
                model_id=label,
                timeout=command_timeout,
                render=render_command,
                handoff_root=handoff_root,
                packet_sha256=digest,
                canaries=load_canaries(root) if backend == "replay" else [],
                failure_log=failure_log,
            )
        except (ProbeError, CvprTierError, HandoffError) as exc:
            errors.append({"error": str(exc), "template": template})
            continue
        calls += 1
        tokens += int((usage or {}).get("estimated_tokens") or len(prompt) // 4)
        try:
            answers = parse_probe_response(text)
        except ProbeError as exc:
            _append_jsonl(failure_log, {"error": str(exc), "handoff_id": batch_id, "kind": "parse", "template": template})
            errors.append({"error": str(exc), "template": template})
            continue
        by_id = {row["handoff_id"]: row for row in answers}
        for item in batch_items:
            gold = next(row for row in key_items if row["handoff_id"] == item["handoff_id"])
            parsed = by_id.get(item["handoff_id"], {"handoff_id": item["handoff_id"]})
            prediction_items.append(
                {
                    "handoff_id": item["handoff_id"],
                    "parsed": {key: parsed.get(key) for key in ("authors", "choices", "tier", "title", "venue", "year")},
                    "scores": score_item(template, parsed, gold),
                    "template": template,
                }
            )
    predictions = {
        "backend": backend,
        "batch_size": batch_size,
        "calls": calls,
        "items": prediction_items,
        "model_id": label,
        "seed": seed,
        "skipped": skipped,
        "templates": list(templates),
        "tokens": tokens,
        "variant": variant,
    }
    _write_json(destination / "predictions.json", predictions)
    _write_json(
        material / "probe_keys" / f"{_key_name(label, variant)}.json",
        {"items": key_items, "model_id": label, "omitted_item_ids": omitted_ids, "variant": variant},
    )
    return {
        "backend": backend,
        "calls": calls,
        "errors": errors,
        "items": len(prediction_items),
        "model_id": label,
        "skipped": skipped,
        "tokens": tokens,
    }


def _invoke(
    backend: str,
    *,
    template: str,
    prompt: str,
    handoff_id: str,
    model_id: str,
    timeout: float,
    render,
    handoff_root: Path | None,
    packet_sha256: str,
    canaries: list[str],
    failure_log: Path,
) -> tuple[str, dict[str, Any] | None]:
    from .cvpr_handoff import HandoffError, export_replay_packet, import_replay_answer, run_isolated_command
    from .cvpr_tier import _append_jsonl

    if backend == "command":
        try:
            result = run_isolated_command(
                template,
                prompt=prompt,
                handoff_id=handoff_id,
                model_id=model_id,
                mode="probe",
                timeout=timeout,
                render=render,
            )
        except HandoffError as exc:
            _append_jsonl(failure_log, {"error": str(exc), "handoff_id": handoff_id, "kind": "command"})
            raise ProbeError(str(exc)) from exc
        usage = {
            "estimated_tokens": result.get("estimated_tokens"),
            "latency_seconds": result.get("latency_seconds"),
        }
        if isinstance(result.get("usage"), dict):
            usage.update(result["usage"])
        return str(result["text"]), usage
    if handoff_root is None:
        raise ProbeError("replay probe needs a handoff directory")
    try:
        exported = export_replay_packet(
            handoff_root,
            handoff_id=handoff_id,
            prompt=prompt,
            packet_sha256=packet_sha256,
            holistic_mode="probe",
            holistic_samples=1,
        )
        text = import_replay_answer(
            exported,
            handoff_id=handoff_id,
            packet_sha256=packet_sha256,
            canaries=canaries,
        )
    except HandoffError as exc:
        raise ProbeError(str(exc)) from exc
    return text, {"estimated_tokens": len(prompt) // 4}


def _key_record(item: dict[str, Any], template: str, questions: list[dict[str, Any]], *, omitted: bool) -> dict[str, Any]:
    return {
        "decop_answers": [question["answer"] for question in questions],
        "handoff_id": item["handoff_id"],
        "item_id": item.get("item_id"),
        "kind": item.get("kind"),
        "omitted": omitted,
        "paper_id": item.get("paper_id"),
        "surnames": list(item.get("surnames") or []),
        "template": template,
        "title": item.get("title") or "",
        "venue": item.get("venue") or "",
        "year": item.get("year"),
    }


def _item_body(template: str, item: dict[str, Any]) -> str:
    if template == "P-A":
        return "Abstract:\n" + str(item.get("abstract") or "").strip()
    if template == "P-T":
        return "Title:\n" + str(item.get("title") or "").strip()
    lines = ["Which passage is copied verbatim? The other three are paraphrases."]
    for index, question in enumerate(item.get("questions") or [], start=1):
        lines.append(f"Question {index}")
        options = question.get("options") or {}
        for letter in "ABCD":
            lines.append(f"{letter}. {options.get(letter, '')}")
    return "\n".join(lines)


def _template_instructions(template: str) -> str:
    common = (
        "Reply with one JSON object of the form "
        '{"items":[{"handoff_id":"","title":"","authors":[],"venue":"","year":null,"tier":"","choices":[]}]}. '
        "Use an empty string and tier \"unknown\" when you do not know. "
        "tier is oral, highlight, poster, reject, or unknown. "
        "choices is empty except for the verbatim task, where it is one letter per question."
    )
    if template == "P-A":
        return "Each item gives an abstract and nothing else. Name the title, authors, venue, year, and presentation tier.\n" + common
    if template == "P-T":
        return "Each item gives a title and nothing else. Name the authors, venue, year, and presentation tier.\n" + common
    return "Each item asks which passage is verbatim.\n" + common


def _template_seed(template: str) -> int:
    return {"P-A": 1, "P-T": 2, "P-D": 3}[template]


def _key_name(model_id: str, variant: str | None) -> str:
    if variant:
        return f"{model_id}.{variant}"
    return model_id


def _authors(value: object) -> list[str]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return [part for part in parts if part]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _choice_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [letter for letter in value.upper() if letter in "ABCD"]
    if isinstance(value, list):
        return [str(item).strip().upper()[:1] for item in value if str(item).strip()]
    return []


def _optional_year(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if year < 1000 or year > 9999:
        return None
    return year


def _year_hit(predicted: object, gold: object) -> bool:
    if predicted is None or gold is None or predicted == "" or gold == "":
        return False
    try:
        return int(predicted) == int(gold)
    except (TypeError, ValueError):
        return False


def _venue_hit(predicted: str, gold: str) -> bool:
    if not predicted.strip() or not gold.strip():
        return False
    left = re.sub(r"[^a-z0-9]+", " ", predicted.lower()).strip()
    right = re.sub(r"[^a-z0-9]+", " ", gold.lower()).strip()
    return bool(left) and (left in right or right in left)


def _text(node: ElementTree.Element, name: str) -> str:
    child = node.find(f"{_ATOM}{name}")
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ProbeError("since must be YYYY-MM-DD") from exc


def _load_passages(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    papers = payload.get("papers") if isinstance(payload, dict) else None
    if not isinstance(papers, dict):
        raise ProbeError("passages file needs a papers object")
    return papers


def _load_control_file(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    papers = payload.get("papers") if isinstance(payload, dict) else payload
    if not isinstance(papers, list):
        raise ProbeError("controls file needs a papers list")
    return [item for item in papers if isinstance(item, dict)]


def _network_get(url: str) -> bytes:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "AutoPaperReview-cvpr-tier"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()
