"""Retrospective, paired description of fixed APR predictions and paper outcomes."""
from pathlib import Path
import hashlib
import json
import math
import os

import numpy as np
from scipy.stats import rankdata, spearmanr

W = Path(__file__).resolve().parents[1]
OUT = W.parent / "outputs" / "提案分与论文终评"
BANDS = [(1, 2.5), (2.5, 3), (3, 3.5), (3.5, 5.01)]
NAMES = ["固定测试", "独立确认", "两组独立复核"]
ORDER = ["fixed", "fresh", "replications"]


def read(path, expected=None):
    data = path.read_bytes()
    if expected is not None:
        assert hashlib.sha256(data).hexdigest() == expected, path
    return json.loads(data)


def load_data():
    source = read(W.parent / "outputs/Proposal评分分布/分布统计.json")["sources"]
    cohorts = {}
    for group, folder in [("fixed", W / "fixed100"), ("fresh", W / "next/confirmation")]:
        expected = source[group]
        lock = read(folder / "lock.json")
        tiers = {r["forum"]: r["tier"] for r in lock["cases"] if r["split"] == "test"}
        preds = read(W / "next" / (group + "-predictions.json"), expected["prediction_sha256"])
        scores = {r["forum"]: r["overall"] for r in preds}
        refs = read(folder / "sealed/references.json", expected["reference_sha256"])
        assert len(refs) == len(scores) == 100 and set(scores) == set(tiers) == {r["forum"] for r in refs}
        cohorts[group] = [(r["forum"], tiers[r["forum"]], scores[r["forum"]], r["prospective"],
                           r["final_rating"], r["accept"]) for r in refs]
    index = {r["key"]: r["forum"] for r in read(W / "reason-transfer/blind-evaluation.json")}
    recalls = read(W / "reason-transfer/available/joined.json", source["replications"]["joined_sha256"])
    cohorts["replications"] = [
        (index[r["key"]], r["group"] + "-" + r["tier"], r["base"], r["target"], r["rating"], r["accept"])
        for r in recalls if r["group"] in ("replication_A", "replication_B") and r["base"] is not None
    ]
    assert len({r[0] for rows in cohorts.values() for r in rows}) == sum(map(len, cohorts.values())) == 399
    return cohorts


def auc(x, decisions):
    known = np.isfinite(decisions)
    y = decisions[known] == 1
    n = int(sum(y))
    return float((rankdata(x[known])[y].sum() - n * (n + 1) / 2) / (n * (len(y) - n)))


def correlation(x, y):
    return float(spearmanr(x, y).statistic)


def interval(x, y, indices):
    a = rankdata(x[indices], axis=1)
    b = rankdata(y[indices], axis=1)
    a -= a.mean(axis=1, keepdims=True)
    b -= b.mean(axis=1, keepdims=True)
    estimates = (a*b).sum(axis=1) / np.sqrt((a*a).sum(axis=1) * (b*b).sum(axis=1))
    return np.quantile(estimates, [.025, .975]).tolist()


def pair_results(x, y):
    a,b = np.triu_indices(len(x), 1)
    gap, diff = x[a]-x[b], y[a]-y[b]
    comparable = diff != 0
    credit = (gap*diff > 0).astype(float) + .5 * (gap == 0)
    def results(mask):
        mask = mask & comparable
        return {"pairs": int(mask.sum()), "ordered_fraction": float(credit[mask].mean())}
    return {"all": results(comparable), "gap_le_0.25": results(abs(gap) <= .25+1e-9),
            "gap_ge_1": results(abs(gap) >= 1-1e-9)}


def top_fifth(x, y):
    k = math.ceil(len(x) / 5)
    cutoff = float(np.sort(x)[-k])
    strict, tied = x > cutoff, x == cutoff
    a, b = int(y[strict].sum()), int(y[tied].sum())
    available = int(tied.sum()); picked = k - int(strict.sum())
    return {"n": len(x), "k": k, "positive": int(y.sum()), "cut": cutoff,
            "included_positive_range": [a + max(0, picked - (available-b)), a + min(picked,b)],
            "random_expected": float(k * y.mean())}


def analyze(rows, seed):
    tiers = np.array([r[1] for r in rows])
    x, idea, final, dec = np.array([r[2:] for r in rows], dtype=float).T
    assert np.isfinite([x, idea, final]).all() and set(dec[np.isfinite(dec)]) == {0, 1}
    rng = np.random.default_rng(seed)
    resamples = []
    for g in np.unique(tiers):
        idx = np.flatnonzero(tiers == g)
        resamples.append(rng.choice(idx, (3000, len(idx))))
    idx = np.column_stack(resamples)
    bands = {}
    for lo, hi in BANDS:
        included = (x >= lo) & (x < hi)
        val = final[included]
        bands[str(lo)] = dict(n=len(val), mean=float(val.mean()), median=float(np.median(val)),
                              quartiles=np.quantile(val, [.25, .75]).tolist())
    return {
        "n": len(x), "decisions_n": int(np.isfinite(dec).sum()),
        "apr_to_final": correlation(x, final), "apr_to_final_ci": interval(x, final, idx),
        "apr_to_idea": correlation(x, idea), "idea_to_final": correlation(idea, final),
        "apr_accept_auc": auc(x, dec), "bands": bands,
        "pairwise_final": pair_results(x, final), "pairwise_idea": pair_results(x, idea),
        "top20pct_final_ge_7": top_fifth(x, final >= 7),
        "top20pct_idea_ge_3p5": top_fifth(x, idea >= 3.5),
    }


def chart(data, statistics):
    os.environ["MPLCONFIGDIR"] = str(W / "score-distributions/mpl-cache")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font = font_manager.FontProperties(fname="/System/Library/Fonts/STHeiti Light.ttc")
    font_manager.fontManager.addfont(font.get_file())
    matplotlib.rcParams.update({"font.family":[font.get_name(),"DejaVu Sans"], "font.size":11})
    figure, axes = plt.subplots(1, 3, figsize=(11.5, 5.1), sharey=True)
    figure.subplots_adjust(left=.069, right=.987, top=.72, bottom=.255, wspace=.09)
    color, muted = "#176878", "#546971"
    figure.text(.067,.91,"提案分越高，论文最终审稿均分整体越高", fontsize=21, color="#18383F")
    figure.text(.067,.83,"每个点代表一篇论文，横向轻微错开以显示重叠。深色横线为同档论文的终评均值。",color=muted)
    for i, (ax, group, name) in enumerate(zip(axes, ORDER, NAMES)):
        rows = data[group]
        s = statistics[group]
        rng = np.random.default_rng(2310+i)
        for j, (lo, hi) in enumerate(BANDS):
            ys = np.array([r[4] for r in rows if lo <= r[2] < hi])
            ax.scatter(j+rng.uniform(-.21,.21,len(ys)),ys,s=13,color="#7E9CA9",alpha=.49,linewidths=0)
            avg = ys.mean()
            ax.hlines(avg,j-.30,j+.30,color=color,linewidth=2.8)
            ax.text(j,-.115,str(len(ys))+" 篇",ha="center",transform=ax.get_xaxis_transform(),fontsize=9,color=muted)
        ax.set_title(f"{name} · {len(rows)} 篇",loc="left",fontsize=13)
        ax.set_xlim(-.51,3.52);ax.set_ylim(0,10)
        ax.set_xticks([0,1,2,3],["<2.5","2.5–<3","3–<3.5","≥3.5"])
        ax.tick_params(axis="both",length=0,pad=4,labelsize=10)
        ax.set_yticks([0,2,4,6,8,10]);ax.set_axisbelow(True);ax.grid(axis="y",color="#DBE3E4",lw=.65)
        for side in ("left","right","top"):ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color("#B0C0C4")
        ax.text(.02,.97,f"Spearman ρ = {s['apr_to_final']:.3f}",ha="left",va="top",transform=ax.transAxes,fontsize=10,color=color)
    axes[0].set_ylabel("论文最终审稿均分（10 分制）")
    figure.text(.53,.111,"APR 提案分（1–5 分）",ha="center",fontsize=12)
    figure.text(.067,.036,"各样本按论文终评分层抽取。图中显示这批样本内部的关系。",fontsize=10,color=muted)
    figure.savefig(OUT / "提案分与论文终评.png",dpi=165,facecolor="white")
    plt.close(figure)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    data = load_data()
    stats = {g: analyze(data[g], 241125+i) for i,g in enumerate(ORDER)}
    official = read(W / "trait-review/incumbent-benchmark.json")
    official["replications"] = read(W / "reason-transfer/available/apr-separate.json")["replications"]["base"]
    for group, s in stats.items():
        assert abs(s["apr_to_final"] - official[group]["apr_rho"]) < 1e-10
        assert abs(s["apr_accept_auc"] - official[group]["apr_auc"]) < 1e-10
        assert sum(v["n"] for v in s["bands"].values()) == s["n"]
    chart(data, stats)
    result = {"方法":"冻结预测与同一论坛对齐。相关系数均为处理并列的 Spearman。区间为按终评抽样层、复核子队列进行 3000 次论文级重采样所得的百分位区间。两两比较剔除参照同分，模型并列计半。前 20% 篇数向上取整，边界并列给出命中数范围。所有分档均为描述性分析。","样本":stats}
    (OUT / "逐队列统计.json").write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+"\n")
    print(json.dumps(stats,ensure_ascii=False))
