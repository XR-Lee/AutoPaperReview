"""Validate the unchanged installed scorer on genuine, directly reviewed proposals."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from random import Random
from statistics import mean, median
import time
import numpy as np

from autopaperreview.hashing import sha256_file
from autopaperreview.proposal.data import digest, read, write
from autopaperreview.proposal.merit import compact
from autopaperreview.proposal.overall_rank import evaluate
from autopaperreview.proposal.priority import load_profile, rank, route
from autopaperreview.proposal.stages import invoke
from autopaperreview.public_calibration import spearman, roc_auc
from transfer import AX, P, PENALTIES

FOLDER = Path("work/next/calibration")
SOURCE = Path("work/source-check")
THRESHOLD = 6.625


def lock_inputs():
    info = read(P / "lock.json")
    profile, _ = load_profile(FOLDER / "profile.json")
    state = {
        "external_lock": sha256_file(P / "lock.json"),
        "script": sha256_file(Path(__file__)),
        "protocol": sha256_file(P / "deployed-protocol.txt"),
        "profile": sha256_file(FOLDER / "profile.json"),
        "assets": profile["assets"],
        "features": profile["ranker"]["names"],
        "n": len(info["frozen"]["cases"]),
        "threshold": THRESHOLD,
    }
    dest = P / "deployed-lock.json"
    if dest.exists():
        assert read(dest)["frozen"] == state
    else:
        write(dest, {"utc": datetime.now(timezone.utc).isoformat(), "frozen": state})
    return info["frozen"]["cases"], state


def score_plans(workers):
    cases, state = lock_inputs()
    profile, folder = load_profile(FOLDER / "profile.json")

    def run(row):
        key = row["key"]
        inputs = read(P / "inputs" / (key + ".json"))
        assert digest(inputs) == row["input_sha256"]
        model, cmd = route("design")
        record = invoke(
            "design", inputs, P / "cache", model, cmd, 240, cached_only=True
        )
        plan = compact(read(record)["output"])
        dest = P / "deployed" / (key + ".json")
        if dest.is_file():
            assert read(dest)["proposal_hash"] == digest(plan)
            return
        answer = rank(plan, P / "deployed", profile, folder)
        write(
            dest,
            {
                "key": key,
                "proposal_hash": digest(plan),
                "overall": answer["overall"],
                "features": {n: answer["features"][n] for n in state["features"]},
                "score_requests": answer["input_hashes"],
            },
        )

    pending = {r["key"]: r for r in cases}
    failures = []
    # Ready markers come from completed anonymous plans; no duplicate compressor calls.
    last = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        active = {}
        while pending or active:
            for k, r in list(pending.items()):
                if len(active) >= workers:
                    break
                if (P / "ratings" / (k + ".json")).is_file():
                    active[pool.submit(run, r)] = k
                    del pending[k]
            for f in [f for f in active if f.done()]:
                k = active.pop(f)
                try:
                    f.result()
                except Exception as exc:
                    failures.append(k)
                    print("failed", k, str(exc)[:180], flush=True)
                last = time.monotonic()
            if not active and pending and time.monotonic() - last > 650:
                raise RuntimeError("missing upstream external plan scores")
            if pending or active:
                time.sleep(2)
    print("complete external production", len(cases), len(failures), flush=True)
    if failures:
        raise RuntimeError("incomplete production predictions")


def agreement(rows):
    x = [r["overall"] for r in rows]
    y = [r["human"] for r in rows]
    pairs = []
    for a, b in combinations(rows, 2):
        if a["topic"] != b["topic"] or a["human"] == b["human"]:
            continue
        diff = (a["human"] - b["human"]) * (a["overall"] - b["overall"])
        pairs.append(float(diff > 0) if diff else 0.5)
    return {
        "n": len(rows),
        "positive": sum(v >= THRESHOLD for v in y),
        "rho": spearman(x, y),
        "auc": roc_auc(x, [int(v >= THRESHOLD) for v in y]),
        "same_topic_comparable_pairs": len(pairs),
        "same_topic_concordance": mean(pairs) if pairs else None,
    }


def train(x, y, idx, p):
    a = x[idx]
    center = np.nanmedian(a, axis=0)
    center = np.nan_to_num(center, nan=0.0)
    a = np.where(np.isnan(a), center, a)
    mu = a.mean(0)
    sd = np.maximum(a.std(0), 0.3)
    z = (a - mu) / sd
    b = np.linalg.solve(
        z.T @ z + p * np.eye(z.shape[1]), z.T @ (y[idx] - y[idx].mean())
    )
    return {
        k: v.tolist()
        for k, v in [("impute", center), ("mean", mu), ("scale", sd), ("weight", b)]
    } | {"bias": float(y[idx].mean())}


def estimate(x, m):
    v = np.where(np.isnan(x), m["impute"], x)
    return ((v - m["mean"]) / m["scale"]) @ m["weight"] + m["bias"]


def out_topic(x, y, topics, idx, penalty):
    pred = np.zeros(len(idx))
    for t in set(topics[idx]):
        hold = topics[idx] == t
        model = train(x, y, idx[~hold], penalty)
        pred[hold] = estimate(x[idx[hold]], model)
    return pred


def choose(x, y, topics, idx):
    return max(
        PENALTIES,
        key=lambda p: (
            spearman(y[idx].tolist(), out_topic(x, y, topics, idx, p).tolist()),
            p,
        ),
    )


def joint(rows):
    y = np.array([r["human"] for r in rows])
    x = np.array([r["x"] for r in rows], dtype=float)
    topics = np.array([r["topic"] for r in rows])
    idx = np.arange(len(x))
    oof = np.zeros(len(x))
    for t in sorted(set(topics)):
        tr = idx[topics != t]
        p = choose(x, y, topics, tr)
        oof[topics == t] = estimate(x[topics == t], train(x, y, tr, p))
    p = choose(x, y, topics, idx)
    return {
        "oof_rho": spearman(y.tolist(), oof.tolist()),
        "oof_mae": mean(abs(y - oof)),
        "penalty": p,
        "model": train(x, y, idx, p),
    }


def summarize(rows):
    out = {
        "overall": agreement(rows),
        "origin": {
            k: agreement([r for r in rows if r["origin"] == k]) for k in ("Human", "AI")
        },
    }
    rng = Random(11752)
    bootstrap = {"rho": [], "auc": []}
    for _ in range(2000):
        sample = rng.choices(rows, k=len(rows))
        x = [r["overall"] for r in sample]
        y = [r["human"] for r in sample]
        for k, v in (
            ("rho", spearman(x, y)),
            ("auc", roc_auc(x, [int(v >= THRESHOLD) for v in y])),
        ):
            if v is not None:
                bootstrap[k].append(v)
    out["paper_bootstrap_95"] = {
        k: [sorted(v)[int(t * (len(v) - 1))] for t in (0.025, 0.975)]
        for k, v in bootstrap.items()
    }
    draws = {"model_rho": [], "human_rho": [], "model_auc": [], "human_auc": []}
    eligible = [r for r in rows if len(r["grades"]) >= 2]
    for _ in range(1000):
        human = []
        reference = []
        model = []
        for r in eligible:
            g = r["grades"]
            i = rng.randrange(len(g))
            human.append(g[i])
            reference.append(mean(g[:i] + g[i + 1 :]))
            model.append(r["overall"])
        for name, v in (("model", model), ("human", human)):
            draws[name + "_rho"].append(spearman(v, reference))
            draws[name + "_auc"].append(
                roc_auc(v, [int(z >= THRESHOLD) for z in reference])
            )
    out["peer_reference"] = {
        "n": len(eligible),
        "median": {k: median(i for i in v if i is not None) for k, v in draws.items()},
    }
    return out


def collect():
    cases, state = lock_inputs()
    assert {f.stem for f in (P / "deployed").glob("*.json")} == {
        r["key"] for r in cases
    }
    targets = read(P / "external-gold.json")
    rows = []
    for r in cases:
        k = r["key"]
        d = read(P / "deployed" / (k + ".json"))
        q = read(P / "ratings" / (k + ".json"))["output"]
        grades = [a["scores"]["overall"] for a in targets[k]]
        rows.append(
            {
                "key": k,
                "overall": d["overall"],
                "grades": grades,
                "human": mean(grades),
                "topic": r["topic"],
                "origin": r["origin"],
                "x": [d["features"][i] for i in state["features"]]
                + [q[i]["score"] for i in AX],
            }
        )
    return rows


def iclr_inputs():
    lock = read(SOURCE / "lock.json")["frozen"]["rows"]
    names = read(FOLDER / "profile.json")["ranker"]["names"]
    rows = []
    for r in lock:
        k = r["key"]
        q = read(Path("work/next/interest") / (k + ".json"))["output"]
        rows.append(
            {
                "key": k,
                "restricted": r["restricted"],
                "baseline": r["baseline"],
                "x": [r["features"][i] for i in names] + [q[i]["score"] for i in AX],
            }
        )
    return rows


def score_iclr(trainrows, blind):
    m = joint(trainrows)["model"]
    return {
        r["key"]: r["baseline"]
        if r["restricted"]
        else float(
            1 + (np.clip(estimate(np.array(r["x"], dtype=float), m), 1, 10) - 1) * 4 / 9
        )
        for r in blind
    }


def evaluate_iclr(refs, scores):
    assert set(scores) == {r["key"] for r in refs}
    result = {}
    for split, n in (("dev", 96), ("val", 48)):
        grp = [
            {**r, "features": {"s": scores[r["key"]]}}
            for r in refs
            if r["split"] == split
        ]
        assert len(grp) == n
        result[split] = evaluate(grp, {"method": "mean", "names": ["s"]})
    result["passes"] = all(
        result[s][m] >= 0.7 for s in ("dev", "val") for m in ("rho", "auc")
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "rate", "summarize", "replay"))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--root", type=Path, default=P)
    p = parser.parse_args()
    if p.action == "freeze":
        lock_inputs()
    elif p.action == "rate":
        score_plans(p.workers)
    else:
        folder = p.root
        if p.action == "summarize":
            rows, blind = collect(), iclr_inputs()
            write(folder / "deployed-external-rows.json", rows)
            write(folder / "deployed-blind-rows.json", blind)
        else:
            rows = read(folder / "deployed-external-rows.json")
            blind = read(folder / "deployed-blind-rows.json")
        write(folder / "deployed-summary.json", summarize(rows))
        write(folder / "joint-fit.json", joint(rows))
        pred = score_iclr(rows, blind)
        write(folder / "joint-frozen-predictions.json", pred)
        if p.action == "summarize":
            refs = [
                {k: r[k] for k in ("key", "split", "target", "rating", "accept")}
                for r in read(Path("work/scale/development.json"))
            ]
            write(folder / "joint-references.json", refs)
        else:
            refs = read(folder / "joint-references.json")
        write(folder / "joint-evaluation.json", evaluate_iclr(refs, pred))
