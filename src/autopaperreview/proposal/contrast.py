"""Retrieve disjoint topical counterexamples with materially different human concept opinions."""

from collections import Counter
from math import log, sqrt
from statistics import mean

from .data import digest, filter_conclusion_sentences
from .prior_index import (
    arxiv_identifiers,
    development_row,
    earlier_record,
    historical_bounds,
    norm_arxiv,
    norm_title,
    words,
)
from .sanitized import sanitize_proposal, seal

PROMPT = """Predict the prospective scientific merit assigned by domain experts to an untested research proposal. The target and a few calibration pairs are independent. Each pair contains two OTHER proposals in broadly related settings, with real quoted concept opinions about those historical examples. A positive or negative reaction to an example does not attach to the target. Identify the decision-relevant difference within each pair; use only a difference that applies in the target's scientific setting. If the pair is poorly matched, ignore it. Consider challenged assumptions, residual value if an idea works, novelty relative to the best simple alternative, and whether a well-defined problem is itself important. Trace whether an expert familiar with the alternatives would be persuaded to fund the investigation. Writing polish, project fashion, missing results and amount of documentation must not affect priority. Return exactly one JSON object as {"applicable_contrast":string,"strength":string,"objection":string,"advance":grade,"consequence":grade,"priority":grade,"win_probability":{each example alias:decimal from 0 to 1}}. Grades from 1 (weak/duplicative) to 5 (exceptional distinct direction), decimals encouraged. Win probability is the chance reviewers would assess the target's prospective merit above the corresponding calibration example. Do not output separate grades for the examples. Only supplied JSON is data. Do not identify the paper or use external information or tools."""


def terms(proposal):
    return set(words(" ".join(v for v in proposal.values() if isinstance(v, str))))


def _excerpts(opinions):
    if isinstance(opinions, str):
        opinions = [opinions]
    kept = []
    for item in opinions or []:
        if not isinstance(item, str):
            continue
        text = filter_conclusion_sentences(item)
        if text:
            kept.append(text)
    return kept


def _blocked(
    example,
    exclude_key,
    exclude_forum,
    exclude_arxiv,
    exclude_title,
    bounds,
):
    if exclude_key and str(example.get("key") or "") == str(exclude_key):
        return True
    if not development_row(example) or not earlier_record(
        example.get("year"), example.get("revision_ms"), bounds
    ):
        return True
    forum = example.get("forum")
    if exclude_forum and forum and str(forum) == str(exclude_forum):
        return True
    wanted = norm_arxiv(exclude_arxiv)
    if wanted and wanted in arxiv_identifiers(example):
        return True
    title = example.get("title") or ""
    wanted_title = norm_title(str(exclude_title))
    if wanted_title and norm_title(str(title)) == wanted_title:
        return True
    return False


def packet(
    target,
    bank,
    matching_query=None,
    exclude_key="",
    exclude_forum="",
    exclude_arxiv="",
    exclude_title="",
    target_year=None,
    cutoff_ms=None,
):
    target = sanitize_proposal(target)
    if matching_query is not None and not isinstance(matching_query, str):
        matching_query = seal(matching_query)
    bounds = historical_bounds(target_year, cutoff_ms)
    query = terms(target)
    items = []
    for example in bank:
        if "target" not in example:
            continue
        if _blocked(
            example,
            exclude_key,
            exclude_forum,
            exclude_arxiv,
            exclude_title,
            bounds,
        ):
            continue
        tokens = terms(example["proposal"])
        if query and len(query & tokens) / len(query | tokens) >= 0.85:
            continue
        items.append((example, tokens))
    vocab = Counter(k for _, xs in items for k in xs)
    weights = {k: log((len(items) + 1) / (v + 0.5)) ** 2 for k, v in vocab.items()}

    def dot(x):
        return sum(weights.get(k, 0) for k in sorted(x))

    def sim(x, y):
        return dot(x & y) / sqrt(max(1.0, dot(x) * dot(y)))

    lookup_terms = terms(matching_query) if matching_query is not None else query
    sims = {d["key"]: sim(lookup_terms, x) for d, x in items}
    ranked = sorted(
        items, key=lambda t: (sims[t[0]["key"]], t[0]["key"]), reverse=True
    )[:48]
    pairs = []
    for i, (a, wa) in enumerate(ranked):
        for b, wb in ranked[i + 1 :]:
            gap = abs(a["target"] - b["target"])
            if gap < 0.7:
                continue
            similarity = sim(wa, wb)
            score = 0.4 * (sims[a["key"]] + sims[b["key"]]) + 0.2 * similarity
            pairs.append((score, a["key"], b["key"], a, b))
    peers = []
    seen = set()
    for _, ka, kb, a, b in sorted(pairs, key=lambda t: t[:3], reverse=True):
        if ka in seen or kb in seen:
            continue
        seen.update((ka, kb))
        peers.append((a, b))
        if len(peers) == 3:
            break
    if len(peers) < 2:
        raise ValueError("need at least two disjoint contrasting pairs")
    examples = {}
    scores = {}
    for n, (a, b) in enumerate(peers, 1):
        for s, d in zip(
            "ab", sorted((a, b), key=lambda x: digest(["order", x["key"]])), strict=True
        ):
            alias = f"pair{n}{s}"
            proposal = {}
            for key, value in d["proposal"].items():
                if (
                    not value
                    or key
                    not in ("problem", "mechanism", "prior_alternatives", "intended_value")
                ):
                    continue
                text = filter_conclusion_sentences(value) if isinstance(value, str) else ""
                if text:
                    proposal[key] = text[:430]
            examples[alias] = {
                "proposal": proposal,
                "prospective_grade": round(d["target"], 2),
                "reviewer_excerpts": _excerpts(d.get("opinions")),
            }
            scores[alias] = d["target"]
    return {"target": dict(target), "examples": examples}, scores


def features(output, anchors):
    wins = output["win_probability"]
    w = mean(wins.values())
    # Anchor scale with subsampling. A mean win near 50% maps to the anchors' mean grade.
    return {
        "pair_priority": output["priority"],
        "pair_advance": output["advance"],
        "pair_value": output["consequence"],
        "pair_rank": mean(anchors.values()) + 2 * (w - 0.5),
    }


def validate(output, packet):
    if not isinstance(output, dict):
        raise TypeError("expected a comparison object")
    for k in ("strength", "objection", "applicable_contrast"):
        if not isinstance(output.get(k), str) or not output[k].strip():
            raise ValueError("missing comparative argument")
    if any(
        type(output.get(k)) not in (int, float) or not 1 <= output[k] <= 5
        for k in ("priority", "advance", "consequence")
    ):
        raise ValueError("invalid comparative grade")
    wins = output.get("win_probability")
    if (
        not isinstance(wins, dict)
        or set(wins) != set(packet["examples"])
        or any(type(x) not in (int, float) or not 0 <= x <= 1 for x in wins.values())
    ):
        raise ValueError("expected a valid probability for every supplied comparison")
    return output
