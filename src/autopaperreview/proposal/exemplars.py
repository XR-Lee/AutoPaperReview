"""Select topical calibration examples from per-paper development references only."""

from collections import Counter
from math import log, sqrt

from .prior_index import words


def tokens(proposal):
    return set(words(" ".join(v for v in proposal.values() if isinstance(v, str))))


def packet(proposal, training, exclude_key=""):
    pool = [r for r in training if r["key"] != exclude_key and r["split"] == "dev"]
    pool.sort(key=lambda r: (r["target"], r["key"]))
    if len(pool) < 8:
        raise ValueError("need at least eight distinct development examples")
    query = tokens(proposal)
    vocab = {r["key"]: tokens(r["proposal"]) for r in pool}
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
        r = max(band, key=lambda r: (similarity(r), r["key"]))
        examples[f"ex{i + 1}"] = {
            "idea": {
                k: (v[:600] if v else None)
                for k, v in r["proposal"].items()
                if k in ("problem", "prior_alternatives", "mechanism", "intended_value")
            },
            "reviewer_mean": round(r["target"], 3),
            "example_opinions": r["excerpts"],
        }
    return {"proposal": proposal, "examples": examples}
