"""Select topical calibration examples from per-paper development references only."""

from collections import Counter
from math import log, sqrt

from .data import filter_conclusion_sentences
from .prior_index import calibration_rows, historical_bounds, proposal_body, words


def tokens(proposal):
    return set(words(" ".join(v for v in proposal.values() if isinstance(v, str))))


def packet(proposal, training, exclude_key="", *, target_year=None, cutoff_ms=None, **exclusions):
    bounds = historical_bounds(target_year, cutoff_ms)
    exclusions = {**exclusions, "exclude_text": exclusions.get("exclude_text") or proposal_body(proposal)}
    pool = sorted(
        calibration_rows(training, bounds, excerpt_field="excerpts", exclude_key=exclude_key, **exclusions),
        key=lambda example: (example[0]["target"], example[0]["key"]),
    )
    if len(pool) < 8:
        raise ValueError("need at least eight distinct development examples")
    query = tokens(proposal)
    vocab = {r["key"]: tokens(r["proposal"]) for r, _ in pool}
    df = Counter(w for ws in vocab.values() for w in ws)
    idf = {w: log((len(pool) + 1) / (n + 1)) + 0.1 for w, n in df.items()}

    def similarity(r):
        terms = vocab[r["key"]]
        return sum(idf[w] ** 2 for w in terms & query) / sqrt(
            max(1, sum(idf[w] ** 2 for w in terms))
        )

    examples = {}
    for i in range(4):
        band = pool[i * len(pool) // 4 : (i + 1) * len(pool) // 4]
        r, dated = max(band, key=lambda example: (similarity(example[0]), example[0]["key"]))
        examples[f"ex{i + 1}"] = {
            "idea": {
                k: (v[:600] if v else None)
                for k, v in r["proposal"].items()
                if k in ("problem", "prior_alternatives", "mechanism", "intended_value")
            },
            "reviewer_mean": round(r["target"], 3),
            "example_opinions": [text for item in dated if (text := filter_conclusion_sentences(item))],
        }
    return {"proposal": proposal, "examples": examples}
