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

from .cvpr_anonymize import _HEADING_PUNCT, abstract_heading_rest, extract_meta, stops_abstract, _left_column
from .cvpr_handoff import grok_json_prompt
from .cvpr_json import JsonOutputError, parse_json_output
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
EXTRACTION_FAIL_PERCENT = 5
_DECOY_YEARS = (2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025)


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


def _abstract_ok(value: str) -> bool:
    words = value.split()
    return len(value) >= 20 and len(words) >= 5


def _collect_abstract(lines: list[str], start: int, lead: str) -> str:
    words = lead.split()[:400]
    for line in lines[start:]:
        if not line.strip():
            continue
        if len(words) >= 400 or stops_abstract(line):
            break
        left = _left_column(line)
        if left:
            words.extend(left.split()[: 400 - len(words)])
    return " ".join(words)


def _abstract_from_columns(text: str) -> str:
    """Layout extract: the heading may share its line with the other column."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        rest = abstract_heading_rest(line)
        if rest is None:
            continue
        return _collect_abstract(lines, index + 1, rest)
    return ""


def _abstract_from_inline(text: str) -> str:
    """Raw extract: ``Abstract.`` or ``Abstract—`` may sit on a shared line."""
    match = re.search(rf"(?i)(?:^|\n)\s*abstract(?!\w)\s*{_HEADING_PUNCT}\s*", text)
    if match is None:
        match = re.search(rf"(?:Abstract|ABSTRACT)\s*{_HEADING_PUNCT}\s*", text)
    if match is None:
        return ""
    return _collect_abstract(text[match.end() :].splitlines(), 0, "")


def extract_abstract(text: str, alternate: str | None = None) -> str:
    """Abstract text, trying a layout reading and then a raw-order reading.

    ``alternate`` is the other pdftotext mode when both extracts exist. An
    empty string means neither reading found a heading.
    """
    for source in (text, alternate):
        if not source or not str(source).strip():
            continue
        for parser in (_abstract_from_columns, _abstract_from_inline):
            found = parser(source)
            if _abstract_ok(found):
                return found
    return ""


def extraction_rate_exceeded(failed: int, attempted: int) -> bool:
    """True when more than 5% of papers failed abstract extraction."""
    if attempted <= 0 or failed < 0:
        return False
    return failed * 100 > attempted * EXTRACTION_FAIL_PERCENT


def _usable_title(meta: dict[str, Any]) -> str:
    title = " ".join(str(meta.get("title") or "").split())
    if not title or len(title) > 300:
        return ""
    return title


def _meta_from_sources(text: str, alternate: str | None) -> dict[str, Any]:
    fallback: dict[str, Any] | None = None
    for source in (text, alternate):
        if not source:
            continue
        meta = extract_meta(source)
        if _usable_title(meta):
            return meta
        if fallback is None and str(meta.get("title") or "").strip():
            fallback = meta
    return fallback or {"authors": [], "surnames": [], "title": ""}


def _paper_seed(paper_id: str, seed: int) -> int:
    digest = sha256_bytes(paper_id.encode("utf-8"))
    return seed + (int(digest[:8], 16) % 10_000_000)


def build_target(
    paper_id: str,
    year: int,
    text: str,
    passages: dict[str, Any] | None = None,
    *,
    alternate: str | None = None,
    seed: int = 0,
) -> tuple[dict[str, Any] | None, str | None]:
    """Operator-side record and a failure reason when extraction does not succeed."""
    if not str(text or "").strip() and not str(alternate or "").strip():
        return None, "missing_text"
    primary = text or alternate or ""
    fallback = alternate if text else None
    abstract = extract_abstract(primary, alternate=fallback)
    meta = _meta_from_sources(primary, fallback)
    title = _usable_title(meta)
    if not abstract:
        return None, "no_abstract"
    if not title:
        return None, "no_title"
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
    supplied = passages or {}
    verbatim = str(supplied.get("verbatim") or "").strip()
    paraphrases = [str(item).strip() for item in (supplied.get("paraphrases") or []) if str(item).strip()]
    if verbatim and len(paraphrases) == 3:
        record["verbatim"] = verbatim
        record["paraphrases"] = paraphrases
    else:
        generated = generate_distinctive_passage(primary, seed=_paper_seed(paper_id, seed), avoid=paper_id)
        if generated is None and fallback:
            generated = generate_distinctive_passage(fallback, seed=_paper_seed(paper_id, seed), avoid=paper_id)
        if generated:
            record["verbatim"] = generated["verbatim"]
            record["paraphrases"] = list(generated["paraphrases"])
    return record, None


def target_from_text(
    paper_id: str,
    year: int,
    text: str,
    passages: dict[str, Any] | None = None,
    *,
    alternate: str | None = None,
    seed: int = 0,
) -> dict[str, Any] | None:
    """Operator-side record. Returns None when the text has no abstract."""
    record, _reason = build_target(paper_id, year, text, passages, alternate=alternate, seed=seed)
    return record


_SYNONYMS = (
    ("counts", "tallies"),
    ("count", "tally"),
    ("shows", "demonstrates"),
    ("show", "demonstrate"),
    ("using", "with"),
    ("method", "approach"),
    ("results", "findings"),
    ("paper", "study"),
    ("model", "network"),
    ("significant", "clear"),
    ("novel", "new"),
    ("propose", "present"),
    ("proposed", "presented"),
    ("improve", "raise"),
    ("improves", "raises"),
    ("image", "picture"),
    ("images", "pictures"),
    ("feature", "descriptor"),
    ("features", "descriptors"),
    ("training", "learning"),
    ("dataset", "collection"),
    ("performance", "accuracy"),
    ("better", "stronger"),
    ("task", "problem"),
    ("cells", "sites"),
    ("cell", "site"),
    ("lattice", "grid"),
    ("recovers", "restores"),
    ("reports", "records"),
    ("paired", "matched"),
)


def _norm_sentence(text: str) -> str:
    return " ".join(text.lower().split())


def _swap_synonyms(sentence: str, limit: int, offset: int) -> str:
    found = [(src, dst) for src, dst in _SYNONYMS if re.search(rf"\b{re.escape(src)}\b", sentence, flags=re.IGNORECASE)]
    if not found:
        return sentence
    chosen = found[offset % len(found) :] + found[: offset % len(found)]
    updated = sentence
    applied = 0
    for src, dst in chosen:
        if applied >= limit:
            break
        revised, count = re.subn(rf"\b{re.escape(src)}\b", dst, updated, count=1, flags=re.IGNORECASE)
        if count and revised != updated:
            updated = revised
            applied += 1
    return updated


def _rotate_clause(sentence: str) -> str:
    if ", and " in sentence:
        left, right = sentence.split(", and ", 1)
        right = right[:-1] if right.endswith(".") else right
        if right and left:
            return f"{right[0].upper()}{right[1:]}, and {left[0].lower()}{left[1:]}."
    words = sentence.rstrip(".").split()
    if len(words) >= 10:
        pivot = 4
        moved = words[pivot:] + words[:pivot]
        text = " ".join(moved)
        return f"{text[0].upper()}{text[1:]}."
    return sentence


def _lead_in(sentence: str) -> str:
    if sentence.startswith("We "):
        return "This work " + sentence[3:]
    if sentence.startswith("This "):
        return "The present study " + sentence[5:]
    if sentence.startswith("The "):
        return "This study's " + sentence[4:]
    return "In this study, " + sentence[0].lower() + sentence[1:]


def _three_paraphrases(verbatim: str, seed: int) -> list[str]:
    raw = [
        _swap_synonyms(verbatim, 2, seed % 5),
        _rotate_clause(verbatim),
        _lead_in(_swap_synonyms(verbatim, 1, (seed + 1) % 5)),
    ]
    results: list[str] = []
    for index, candidate in enumerate(raw):
        text = " ".join(candidate.split())
        if not text.endswith("."):
            text += "."
        if _norm_sentence(text) == _norm_sentence(verbatim) or any(_norm_sentence(text) == _norm_sentence(prev) for prev in results):
            words = verbatim.rstrip(".").split()
            pivot = min(len(words) - 1, 3 + index)
            spun = words[pivot:] + words[:pivot]
            text = " ".join(spun).rstrip(".")
            text = f"{text[0].upper()}{text[1:]}."
        results.append(text)
    return results


_PASSAGE_STOP = re.compile(r"(?i)^(?:\d+(?:\.\d+)*\.?\s*)?(?:references|bibliography)\b")
_PASSAGE_HEAD = re.compile(
    r"(?i)^(?:\d+(?:\.\d+)*\.?\s*)?(?:introduction|related\s+work|conclusion|experiments|"
    r"method|methods|approach|appendix|supplementary(?:\s+material)?)\b"
)


def _flush_passage(buf: list[str], out: list[tuple[str, str]], zone: str) -> None:
    text = " ".join(" ".join(buf).split())
    buf.clear()
    if text:
        out.append((zone, text))


def _split_sentences(paragraph: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", paragraph.strip())
    return [part.strip() for part in parts if part.strip()]


def _passage_paragraphs(text: str) -> list[tuple[str, str]]:
    """Left and right columns stay separate paragraphs. Headings start a new zone."""
    zone = "preamble"
    left: list[str] = []
    right: list[str] = []
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            had_prose = bool(left or right)
            _flush_passage(left, out, zone)
            _flush_passage(right, out, zone)
            if zone == "abstract" and had_prose:
                zone = "body"
            continue
        left_text = _left_column(line)
        if _PASSAGE_STOP.match(left_text) or _PASSAGE_STOP.match(stripped):
            break
        heading = abstract_heading_rest(line)
        if heading is not None or stops_abstract(line) or _PASSAGE_HEAD.match(left_text):
            _flush_passage(left, out, zone)
            _flush_passage(right, out, zone)
            if heading is not None:
                zone = "abstract"
                if heading:
                    left.append(heading)
            else:
                zone = "body"
            parts = [part.strip() for part in re.split(r"\s{3,}", stripped, maxsplit=1)]
            if len(parts) == 2:
                other = parts[1]
                if (
                    other
                    and abstract_heading_rest(other) is None
                    and not stops_abstract(other)
                    and not _PASSAGE_HEAD.match(other)
                    and not _PASSAGE_STOP.match(other)
                ):
                    right.append(other)
            continue
        parts = [part.strip() for part in re.split(r"\s{3,}", stripped, maxsplit=1)]
        if len(parts) == 2:
            if parts[0]:
                left.append(parts[0])
            if parts[1]:
                right.append(parts[1])
        else:
            _flush_passage(right, out, zone)
            if parts[0]:
                left.append(parts[0])
    _flush_passage(left, out, zone)
    _flush_passage(right, out, zone)
    return out


def _candidate_sentences(text: str, avoid: str) -> list[str]:
    """Body sentences first. Columns are not joined, so a heading cannot glue them together."""
    abstract = extract_abstract(text)
    abstract_keys = {_norm_sentence(abstract)} if abstract else set()
    if abstract:
        abstract_keys.update(_norm_sentence(part) for part in _split_sentences(abstract))

    def _keep(sentence: str) -> bool:
        if avoid and re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(avoid)}(?![A-Za-z0-9_])", sentence):
            return False
        words = sentence.split()
        if len(words) < 8 or len(words) > 60:
            return False
        if sentence.lower().startswith(("abstract", "references", "acknowledg")):
            return False
        return True

    body: list[str] = []
    abstract_sentences: list[str] = []
    for zone, paragraph in _passage_paragraphs(text):
        if zone == "preamble":
            continue
        for sentence in _split_sentences(paragraph):
            if not _keep(sentence):
                continue
            if zone == "body" and _norm_sentence(sentence) not in abstract_keys:
                body.append(sentence)
            else:
                abstract_sentences.append(sentence)
    if body:
        return body
    if abstract_sentences:
        return abstract_sentences
    return [sentence for sentence in _split_sentences(abstract) if _keep(sentence)]


def _distinct_score(sentence: str) -> int:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", sentence)
    long_words = sum(1 for token in tokens if len(token) >= 8)
    digits = len(re.findall(r"\d", sentence))
    return long_words * 3 + digits + len({token.lower() for token in tokens})


def generate_distinctive_passage(text: str, *, seed: int = 0, avoid: str = "") -> dict[str, Any] | None:
    """One verbatim passage and three paraphrases for a P-D item.

    The passage is taken from the manuscript. Paraphrases are deterministic
    rewrites, so no model call is required. Returns None when the text has
    no usable sentence.
    """
    candidates = _candidate_sentences(text, avoid)
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda sentence: (-_distinct_score(sentence), sentence))
    verbatim = ranked[0]
    if not verbatim.endswith("."):
        verbatim = verbatim.rstrip(".!?") + "."
    paraphrases = _three_paraphrases(verbatim, seed)
    if any(_norm_sentence(item) == _norm_sentence(verbatim) for item in paraphrases):
        return None
    if len({_norm_sentence(item) for item in paraphrases}) < 3:
        return None
    return {"paraphrases": paraphrases, "verbatim": verbatim}


_DECOY_GIVEN = ("Mara", "Jonas", "Priya", "Hugo", "Elena", "Owen", "Nadia", "Felix", "Leila", "Soren")
_DECOY_FAMILY = ("Ellison", "Keller", "Raman", "Bennett", "Voss", "Hartley", "Okada", "Moreau", "Ibarra", "Petrov")
_DECOY_ADJECTIVES = (
    "Adaptive",
    "Causal",
    "Dense",
    "Equivariant",
    "Hierarchical",
    "Incremental",
    "Lightweight",
    "Multimodal",
    "Probabilistic",
    "Self-Supervised",
    "Sparse",
    "Unsupervised",
)
_DECOY_NOUNS = (
    "Appearance",
    "Correspondence",
    "Depth",
    "Geometry",
    "Motion",
    "Occlusion",
    "Radiance",
    "Texture",
    "Trajectory",
    "Viewpoint",
)
_DECOY_TASKS = (
    "Calibration",
    "Completion",
    "Detection",
    "Forecasting",
    "Localization",
    "Matching",
    "Reconstruction",
    "Recognition",
    "Segmentation",
    "Tracking",
)
_DECOY_METHODS = (
    "Attention",
    "Contrastive Learning",
    "Diffusion",
    "Graph Networks",
    "Memory Banks",
    "Neural Fields",
    "Sparse Convolutions",
    "Test-Time Training",
)
_DECOY_PATTERNS = (
    "{adj} {noun} {task} with {method}",
    "{method} for {adj} {noun} {task}",
    "Learning {adj} {noun} {task} via {method}",
    "{noun} {task} by {adj} {method}",
    "Towards {adj} {task} of {noun} using {method}",
)


def _lexicon_title(rng: random.Random) -> tuple[str, str, str, str]:
    adjective = rng.choice(_DECOY_ADJECTIVES)
    noun = rng.choice(_DECOY_NOUNS)
    task = rng.choice(_DECOY_TASKS)
    method = rng.choice(_DECOY_METHODS)
    title = rng.choice(_DECOY_PATTERNS).format(adj=adjective, noun=noun, task=task, method=method)
    return title, noun, task, method


def _recombine_titles(rng: random.Random, titles: list[str]) -> str | None:
    halves: list[tuple[list[str], list[str]]] = []
    for title in titles:
        words = [word for word in title.split() if word]
        if len(words) < 4:
            continue
        mid = len(words) // 2
        halves.append((words[:mid], words[mid:]))
    if len(halves) < 2:
        return None
    left_index = rng.randrange(len(halves))
    right_index = rng.randrange(len(halves))
    if right_index == left_index:
        right_index = (right_index + 1) % len(halves)
    return " ".join([*halves[left_index][0], *halves[right_index][1]])


def _title_key(title: str) -> str:
    from .cvpr_tier import normalize_title

    return normalize_title(title)


def _title_blocked(title: str, norms: set[str], token_keys: set[tuple[str, ...]], digests: set[str]) -> bool:
    key = _title_key(title)
    if not key or key in norms:
        return True
    tokens = tuple(sorted(key.split()))
    if tokens in token_keys:
        return True
    if digests and sha256_bytes(key.encode("utf-8")) in digests:
        return True
    return False


def _decoy_prose(title: str, noun: str, task: str, method: str, *, seed: int) -> tuple[str, str, list[str]]:
    verbatim = (
        f"The {method.lower()} model recovers {noun.lower()} {task.lower()} "
        "from paired views and reports one comparison table."
    )
    abstract = (
        f"{title} addresses {noun.lower()} {task.lower()} with {method.lower()}. "
        f"{verbatim} "
        "Held-out pairs follow the same protocol in every split."
    )
    return abstract, verbatim, _three_paraphrases(verbatim, seed)


def synthetic_decoys(
    count: int,
    *,
    seed: int,
    catalog_titles: list[str] | None = None,
    blocked_titles: list[str] | None = None,
    catalog_digests: set[str] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Plausible stand-ins. Titles are recombined and rejected when they match the catalog.

    ``catalog_titles`` may be recombined into new titles. ``blocked_titles`` and
    ``catalog_digests`` are collision checks only. Digests are SHA-256 of
    ``normalize_title`` and are not titles. No network.
    """
    if count < 0:
        raise ProbeError("decoy count must be non-negative")
    sources = [title.strip() for title in (catalog_titles or []) if title and title.strip()]
    blocked = [*sources, *(title.strip() for title in (blocked_titles or []) if title and str(title).strip())]
    norms = {_title_key(title) for title in blocked}
    norms.discard("")
    token_keys = {tuple(sorted(key.split())) for key in norms if key}
    digests = {str(item) for item in (catalog_digests or []) if re.fullmatch(r"[0-9a-f]{64}", str(item))}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index in range(count):
        chosen: tuple[str, str, str, str] | None = None
        for attempt in range(48):
            rng = random.Random(f"{seed}:decoy:{index}:{attempt}")
            noun = rng.choice(_DECOY_NOUNS)
            task = rng.choice(_DECOY_TASKS)
            method = rng.choice(_DECOY_METHODS)
            title: str | None
            if sources and attempt % 2 == 0:
                title = _recombine_titles(rng, sources)
                if title is None:
                    title, noun, task, method = _lexicon_title(rng)
            else:
                title, noun, task, method = _lexicon_title(rng)
            key = _title_key(title)
            if not title or key in seen or _title_blocked(title, norms, token_keys, digests):
                continue
            seen.add(key)
            chosen = (title, noun, task, method)
            break
        if chosen is None:
            raise ProbeError("could not build a decoy title outside the catalog")
        title, noun, task, method = chosen
        rng = random.Random(f"{seed}:decoy-meta:{index}")
        given = rng.choice(_DECOY_GIVEN)
        family = rng.choice(_DECOY_FAMILY)
        other_given = rng.choice(_DECOY_GIVEN)
        other_family = rng.choice(_DECOY_FAMILY)
        if other_family == family:
            other_family = _DECOY_FAMILY[( _DECOY_FAMILY.index(family) + 1) % len(_DECOY_FAMILY)]
        authors = [f"{given} {family}", f"{other_given} {other_family}"]
        abstract, verbatim, paraphrases = _decoy_prose(title, noun, task, method, seed=seed + index)
        rows.append(
            {
                "abstract": abstract,
                "authors": authors,
                "item_id": f"decoy-{index:02d}",
                "kind": "decoy",
                "paper_id": f"decoy-{index:02d}",
                "paraphrases": paraphrases,
                "surnames": surnames_of(authors),
                "title": title,
                "venue": "CVPR",
                "verbatim": verbatim,
                "year": rng.choice(_DECOY_YEARS),
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


def _kind_buckets(
    targets: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    decoys: list[dict[str, Any]],
    *,
    primary_year: int | None,
) -> dict[str, list[dict[str, Any]]]:
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
    return buckets


def assemble_batches(
    targets: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    decoys: list[dict[str, Any]],
    *,
    size: int = 10,
    seed: int = 0,
    primary_year: int | None = None,
) -> list[list[dict[str, Any]]]:
    """Shuffle every item into batches of ``size``. Nothing is left over.

    Kinds are dealt round-robin after a seeded shuffle, then each batch is
    shuffled again with that same generator. The same seed reproduces the
    same batches.
    """
    if size < 1:
        raise ProbeError("batch size must be positive")
    buckets = _kind_buckets(targets, controls, decoys, primary_year=primary_year)
    rng = random.Random(seed)
    order = ("target", "control", "decoy", "other_year")
    for name in order:
        rng.shuffle(buckets[name])
    stream: list[dict[str, Any]] = []
    while any(buckets[name] for name in order):
        for name in order:
            if buckets[name]:
                stream.append(buckets[name].pop(0))
    batches: list[list[dict[str, Any]]] = []
    for start in range(0, len(stream), size):
        chunk = list(stream[start : start + size])
        rng.shuffle(chunk)
        batches.append(chunk)
    return batches


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


def parse_probe_response(
    text: str, *, expected_handoff_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    try:
        return parse_json_output(text, lambda payload: _parse_probe_items(payload, expected_handoff_ids))
    except JsonOutputError as exc:
        raise ProbeError(f"probe answer is {exc}") from exc


def _parse_probe_items(payload: Any, expected_handoff_ids: set[str] | None) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "items" in payload:
        items = payload["items"]
    elif isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = [payload]
    else:
        raise ProbeError("probe answer has no items")
    if not isinstance(items, list) or not items:
        raise ProbeError("probe items must be a non-empty list")
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ProbeError("each probe item must be an object")
        handoff_id = str(item.get("handoff_id") or "").strip().lower()
        if not _HANDOFF.fullmatch(handoff_id) or handoff_id in seen:
            raise ProbeError("probe items need unique 12-hex handoff ids")
        seen.add(handoff_id)
        for field in ("title", "venue", "tier"):
            if item.get(field) is not None and not isinstance(item[field], str):
                raise ProbeError(f"probe {field} must be a string")
        for field in ("authors", "choices"):
            value = item.get(field)
            if value is not None and not (
                isinstance(value, str)
                or isinstance(value, list) and all(isinstance(part, str) for part in value)
            ):
                raise ProbeError(f"probe {field} must be a string or a list of strings")
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
    if expected_handoff_ids is not None and seen != expected_handoff_ids:
        raise ProbeError("probe handoff ids do not match the requested batch (missing or unexpected items)")
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


def _load_paper_texts(folder: Path, paper_id: str) -> tuple[str | None, str | None]:
    def _read(path: Path) -> str | None:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    layout = _read(folder / f"{paper_id}.layout.txt")
    raw = _read(folder / f"{paper_id}.raw.txt")
    plain = _read(folder / f"{paper_id}.txt")
    if layout is None and raw is None:
        return plain, None
    primary = layout if layout is not None else plain
    alternate = raw
    if alternate is None and plain is not None and plain != primary:
        alternate = plain
    if primary is None:
        return alternate, None
    return primary, alternate


def _extraction_report(attempted: int, failures: list[dict[str, str]]) -> dict[str, Any]:
    failed = len(failures)
    return {
        "attempted": attempted,
        "failed": failed,
        "failure_rate": 0.0 if attempted == 0 else failed / attempted,
        "paper_ids": [row["paper_id"] for row in failures],
        "reasons": {row["paper_id"]: row["reason"] for row in failures},
    }


def _write_extraction(
    manifest_path: Path,
    destination: Path,
    extraction: dict[str, Any],
    write_json: Callable[..., None],
    *,
    model_id: str,
    variant: str | None,
) -> None:
    from pydantic import ValidationError

    from .cvpr_tier import SampleManifest

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["probe_extraction"] = extraction
    try:
        checked = SampleManifest.model_validate(payload)
    except ValidationError:
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        manifest_path.write_text(checked.model_dump_json(indent=2) + "\n", encoding="utf-8")
    write_json(
        destination / "manifest.json",
        {"extraction": extraction, "model_id": model_id, "variant": variant},
    )


def _meta_titles(material: Path) -> list[str]:
    folder = material / "meta"
    if not folder.is_dir():
        return []
    titles: list[str] = []
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("title"):
            titles.append(str(payload["title"]))
    return titles


def titles_from_catalog_payload(payload: Any) -> tuple[list[str], set[str]]:
    """Plaintext titles and title digests from a catalog, virtual JSON, or digest file."""
    if isinstance(payload, dict) and isinstance(payload.get("digests"), list) and not any(
        key in payload for key in ("papers", "results")
    ):
        return [], _digest_set(payload.get("digests"))
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("papers", "results"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    titles: list[str] = []
    for row in rows:
        if isinstance(row, str) and row.strip():
            titles.append(row.strip())
            continue
        if not isinstance(row, dict):
            continue
        for key in ("title", "name"):
            value = row.get(key)
            if value and str(value).strip():
                titles.append(str(value).strip())
                break
    return titles, set()


def _digest_set(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(item) for item in values if re.fullmatch(r"[0-9a-f]{64}", str(item))}


def _load_digests(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(payload, dict):
        return _digest_set(payload.get("digests"))
    return _digest_set(payload)


def _catalog_blocklist(cache: Path, catalog_path: Path | None) -> tuple[list[str], list[str], set[str]]:
    """Recombination titles, extra blocked titles, and catalog digests.

    Plaintext titles from ``catalog.json`` are blocked and are not recombined,
    so a note stored beside the papers cannot become a decoy fragment.
    """
    explicit: list[str] = []
    blocked: list[str] = []
    digests = _load_digests(cache / "title_digests.json")
    if catalog_path is not None and catalog_path.is_file():
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        titles, extra = titles_from_catalog_payload(payload)
        explicit.extend(titles)
        digests |= extra
    catalog_file = cache / "catalog.json"
    if catalog_file.is_file():
        try:
            payload = json.loads(catalog_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            titles, extra = titles_from_catalog_payload(payload)
            blocked.extend(titles)
            digests |= extra
    return explicit, blocked, digests


def _ensure_passage(item: dict[str, Any], *, seed: int) -> bool:
    verbatim = str(item.get("verbatim") or "").strip()
    paraphrases = [str(part).strip() for part in (item.get("paraphrases") or []) if str(part).strip()]
    if verbatim and len(paraphrases) == 3:
        item["verbatim"] = verbatim
        item["paraphrases"] = paraphrases
        return True
    source = str(item.get("abstract") or "")
    generated = generate_distinctive_passage(source, seed=seed, avoid=str(item.get("paper_id") or ""))
    if generated is None:
        return False
    item["verbatim"] = generated["verbatim"]
    item["paraphrases"] = list(generated["paraphrases"])
    return True


def _load_complete_batch(path: Path, *, digest: str, seed: int) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("complete"):
        return None
    if payload.get("prompt_sha256") != digest or payload.get("seed") != seed:
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


def _as_answer(row: dict[str, Any]) -> dict[str, Any]:
    parsed = row.get("parsed")
    if isinstance(parsed, dict):
        answer = dict(parsed)
        answer["handoff_id"] = row.get("handoff_id")
        return answer
    return row


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
    catalog_path: Path | None = None,
    set_id: str | None = None,
    variant: str | None = None,
    handoff_dir: Path | None = None,
    custody: Path | None = None,
) -> dict[str, Any]:
    """Run probe batches. Does not open heldout tiers or custody.

    Catalog titles are not copied into prompts. Collision checks use
    ``title_digests.json`` (hashes only) and any titles already on the sample.
    """
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
    failures: list[dict[str, str]] = []
    papers = list(manifest.get("papers") or [])
    for paper in papers:
        paper_id = str(paper["paper_id"])
        primary, alternate = _load_paper_texts(material / "raw", paper_id)
        if primary is None and alternate is None:
            failures.append({"paper_id": paper_id, "reason": "missing_text"})
            continue
        record, reason = build_target(
            paper_id,
            int(paper["year"]),
            primary or "",
            passages.get(paper_id),
            alternate=alternate,
            seed=seed,
        )
        if record is None:
            failures.append({"paper_id": paper_id, "reason": reason or "no_abstract"})
            continue
        targets.append(record)
    attempted = len(papers)
    extraction = _extraction_report(attempted, failures)
    destination = material / "probes" / label
    if variant:
        destination = destination / variant
    destination.mkdir(parents=True, exist_ok=True)
    _write_extraction(manifest_path, destination, extraction, _write_json, model_id=label, variant=variant)
    if extraction_rate_exceeded(extraction["failed"], attempted):
        _write_json(
            destination / "predictions.json",
            {
                "backend": backend,
                "extraction": extraction,
                "items": [],
                "model_id": label,
                "seed": seed,
                "skipped": [row["paper_id"] for row in failures],
                "templates": list(templates),
                "variant": variant,
            },
        )
        raise ProbeError(
            f"abstract extraction failed for {extraction['failed']} of {attempted} papers "
            f"({extraction['failure_rate']:.1%}); refusing to probe above a 5% failure rate"
        )
    blocked_titles = [str(item.get("title") or "") for item in targets]
    blocked_titles.extend(_meta_titles(material))
    explicit_titles, extra_blocked, digest_set = _catalog_blocklist(root, Path(catalog_path) if catalog_path else None)
    blocked_titles.extend(extra_blocked)
    decoys = synthetic_decoys(
        decoy_count,
        seed=seed,
        catalog_titles=explicit_titles,
        blocked_titles=blocked_titles,
        catalog_digests=digest_set,
    )
    if not targets and not controls and not decoys:
        raise ProbeError("probe batch is empty")
    salt = load_or_create_salt(root)
    key_items: list[dict[str, Any]] = []
    prediction_items: list[dict[str, Any]] = []
    omitted_ids: dict[str, list[str]] = {}
    errors: list[dict[str, str]] = []
    passage_failures: list[dict[str, str]] = []
    batch_log: list[dict[str, Any]] = []
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
    keys_path = material / "probe_keys" / f"{_key_name(label, variant)}.json"

    def _flush() -> None:
        predictions = {
            "backend": backend,
            "batch_size": batch_size,
            "batches": batch_log,
            "calls": calls,
            "extraction": extraction,
            "items": prediction_items,
            "model_id": label,
            "passage_failures": passage_failures,
            "seed": seed,
            "skipped": [row["paper_id"] for row in failures],
            "templates": list(templates),
            "tokens": tokens,
            "variant": variant,
        }
        _write_json(destination / "predictions.json", predictions)
        _write_json(
            keys_path,
            {
                "items": key_items,
                "model_id": label,
                "omitted_item_ids": omitted_ids,
                "passage_failures": passage_failures,
                "variant": variant,
            },
        )
        _write_json(
            destination / "manifest.json",
            {
                "batch_size": batch_size,
                "batches": batch_log,
                "extraction": extraction,
                "model_id": label,
                "passage_failures": passage_failures,
                "seed": seed,
                "variant": variant,
            },
        )

    for template in templates:
        batches = assemble_batches(
            targets,
            controls,
            decoys,
            size=batch_size,
            seed=seed + _template_seed(template),
        )
        omitted_ids[template] = []
        for batch_index, selected in enumerate(batches):
            batch_items: list[dict[str, Any]] = []
            for offset, item in enumerate(selected):
                prepared = dict(item)
                if template == "P-D" and not _ensure_passage(prepared, seed=_paper_seed(str(item["item_id"]), seed)):
                    passage_failures.append({"item_id": str(item["item_id"]), "template": template})
                    continue
                source = f"probe:{template}:{item['item_id']}"
                prepared["handoff_id"] = handoff_id_for(salt, source)
                questions: list[dict[str, Any]] = []
                if template == "P-D":
                    questions = make_decop_questions(
                        str(prepared["verbatim"]),
                        list(prepared["paraphrases"]),
                        seed=seed + batch_index * 100 + offset,
                    )
                    prepared["questions"] = questions
                batch_items.append(prepared)
            if not batch_items:
                continue
            prompt = grok_json_prompt(render_batch_prompt(template, batch_items), label)
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
            batch_id = handoff_id_for(salt, f"batch:{template}:{batch_index}:{digest}")
            batch_path = destination / "batches" / f"{template}-{batch_index:03d}.json"
            stored = _load_complete_batch(batch_path, digest=digest, seed=seed)
            stored_by_id = {str(row.get("handoff_id")): _as_answer(row) for row in stored} if stored else {}
            resumed = bool(stored) and set(stored_by_id) == {item["handoff_id"] for item in batch_items}
            if not resumed:
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
                    errors.append({"batch": str(batch_index), "error": str(exc), "template": template})
                    _flush()
                    continue
                calls += 1
                tokens += int((usage or {}).get("estimated_tokens") or len(prompt) // 4)
                try:
                    answers = parse_probe_response(text, expected_handoff_ids={item["handoff_id"] for item in batch_items})
                except ProbeError as exc:
                    _append_jsonl(
                        failure_log,
                        {"error": str(exc), "handoff_id": batch_id, "kind": "parse", "template": template},
                    )
                    errors.append({"batch": str(batch_index), "error": str(exc), "template": template})
                    _flush()
                    continue
                stored_by_id = {str(row.get("handoff_id")): _as_answer(row) for row in answers}
            batch_predictions: list[dict[str, Any]] = []
            for item in batch_items:
                questions = list(item.get("questions") or [])
                gold = _key_record(item, template, questions, omitted=False)
                key_items.append(gold)
                parsed = stored_by_id.get(item["handoff_id"], {"handoff_id": item["handoff_id"]})
                prediction = {
                    "handoff_id": item["handoff_id"],
                    "parsed": {key: parsed.get(key) for key in ("authors", "choices", "tier", "title", "venue", "year")},
                    "scores": score_item(template, parsed, gold),
                    "template": template,
                }
                prediction_items.append(prediction)
                batch_predictions.append(prediction)
            if not resumed:
                _write_json(
                    batch_path,
                    {
                        "batch_index": batch_index,
                        "complete": True,
                        "items": batch_predictions,
                        "prompt_sha256": digest,
                        "seed": seed,
                        "template": template,
                    },
                )
            batch_log.append(
                {
                    "batch_index": batch_index,
                    "complete": True,
                    "items": len(batch_items),
                    "resumed": resumed,
                    "template": template,
                }
            )
            _flush()
    return {
        "backend": backend,
        "batches": len(batch_log),
        "calls": calls,
        "errors": errors,
        "extraction": extraction,
        "items": len(prediction_items),
        "model_id": label,
        "passage_failures": passage_failures,
        "skipped": [row["paper_id"] for row in failures],
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
