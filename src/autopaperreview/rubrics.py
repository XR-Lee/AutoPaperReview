"""Anchored 2/4/6/8/10 clause table for the seven SAR dimensions.

`sar_bands_v1.md` is the prompt-facing copy. This module is the validator.
A score may omit a clause. Once a clause id is set, the score must equal it.
"""

from __future__ import annotations

COEFFICIENT_SET = "apr-iclr-cap-v2"
RUBRIC_ID = "sar_bands_v1"
DIMENSION_BANDS = (2.0, 4.0, 6.0, 8.0, 10.0)
ICLR_BINS = (1, 3, 5, 6, 8, 10)

# clause id -> (dimension value, score, english, chinese)
CLAUSES: dict[str, tuple[str, float, str, str]] = {
    "cs-2": (
        "claims_supported",
        2.0,
        "Headline claim contradicted by the paper's own table or figure.",
        "标题主张被论文自己的表或图否定。",
    ),
    "cs-4": (
        "claims_supported",
        4.0,
        "Headline uses a proxy, a copied baseline, or a figure that answers a different question.",
        "标题主张使用了代理、抄录基线，或一张回答了另一个问题的图。",
    ),
    "cs-6": (
        "claims_supported",
        6.0,
        "The matched internal comparison supports a qualified claim; a secondary claim is overstated.",
        "匹配的内部比较支持一个限定后的主张；次要主张说大了。",
    ),
    "cs-8": (
        "claims_supported",
        8.0,
        "The claim a reader remembers is the claim the matched comparison supports, including a supported negative result.",
        "读者会记住的主张就是匹配比较所支持的主张，包括得到支持的负面结果。",
    ),
    "cs-10": (
        "claims_supported",
        10.0,
        "Pre-registered claim, matched setting, uncertainty, and the display agrees.",
        "预先注册的主张、匹配设定、不确定性，以及展示与之一致。",
    ),
    "es-2": (
        "experimental_soundness",
        2.0,
        "Protocol is not one experiment, or leakage decides the result.",
        "协议还不是一个实验，或泄漏决定了结果。",
    ),
    "es-4": (
        "experimental_soundness",
        4.0,
        "Protocol is defined, and a confound or an unfair baseline sits on the decision comparison.",
        "协议已写明，但决定性比较上有混淆或不公平基线。",
    ),
    "es-6": (
        "experimental_soundness",
        6.0,
        "Ablations exist; uncertainty is missing, or external numbers are copied beside an otherwise clean internal run.",
        "有消融；缺不确定性，或干净的内部运行旁边抄了外部数字。",
    ),
    "es-8": (
        "experimental_soundness",
        8.0,
        "Matched controls, uncertainty, and the decision comparison is in-stack.",
        "有匹配对照、不确定性，且决定性比较在同一代码栈内。",
    ),
    "es-10": (
        "experimental_soundness",
        10.0,
        "The es-8 conditions plus a released protocol card.",
        "满足 es-8，并且发布了协议说明。",
    ),
    "or-2": (
        "originality",
        2.0,
        "Restatement of a cited method.",
        "复述已引用的方法。",
    ),
    "or-4": (
        "originality",
        4.0,
        "A module swap on a public baseline.",
        "在公开基线上更换模块。",
    ),
    "or-6": (
        "originality",
        6.0,
        "A specific combination whose pieces already exist.",
        "部件都已存在的一个具体组合。",
    ),
    "or-8": (
        "originality",
        8.0,
        "A task or control that changes what a matched comparison means.",
        "改变了匹配比较含义的任务或对照。",
    ),
    "or-10": (
        "originality",
        10.0,
        "A problem definition others can adopt without this method.",
        "别人可以不采用本方法就采纳的问题定义。",
    ),
    "im-2": (
        "importance_of_research_question",
        2.0,
        "No stated user of the result.",
        "没有写明谁会使用这个结果。",
    ),
    "im-4": (
        "importance_of_research_question",
        4.0,
        "A real task whose result is too small to change practice even if true.",
        "任务真实，但即使为真也小到改变不了实践。",
    ),
    "im-6": (
        "importance_of_research_question",
        6.0,
        "A community would use a confirmed version.",
        "若得到确认，社区会使用。",
    ),
    "im-8": (
        "importance_of_research_question",
        8.0,
        "The decision result, if it holds, changes a benchmark people already run.",
        "决定性结果若成立，会改变人们已经在跑的基准。",
    ),
    "im-10": (
        "importance_of_research_question",
        10.0,
        "The question blocks a line of work and the paper makes it measurable.",
        "这个问题卡住了一条研究线，而论文使它可测。",
    ),
    "wc-2": (
        "writing_clarity",
        2.0,
        "A reader cannot reconstruct the protocol, or a lead display contradicts the text.",
        "读者无法重建协议，或主展示与正文矛盾。",
    ),
    "wc-4": (
        "writing_clarity",
        4.0,
        "Protocol is reconstructable and a lead figure or equation is broken or misleading.",
        "协议可重建，但主图或公式损坏或误导。",
    ),
    "wc-6": (
        "writing_clarity",
        6.0,
        "The main argument is checkable; one display is easy to misread.",
        "主论证可以核对，有一处展示容易误读。",
    ),
    "wc-8": (
        "writing_clarity",
        8.0,
        "A first-time reader can check the decision comparison from the main text.",
        "第一次阅读的人可以从正文核对决定性比较。",
    ),
    "wc-10": (
        "writing_clarity",
        10.0,
        "The display, the notation, and the limitations agree.",
        "展示、符号与局限性一致。",
    ),
    "cv-2": (
        "community_value",
        2.0,
        "Nothing reusable is stated.",
        "没有写明可复用的东西。",
    ),
    "cv-4": (
        "community_value",
        4.0,
        "A protocol or split would be useful and is not released.",
        "协议或划分会有用，但没有发布。",
    ),
    "cv-6": (
        "community_value",
        6.0,
        "A reusable task split or negative lesson is stated with enough detail to repeat.",
        "可复用的任务划分或负面教训写得足以重复。",
    ),
    "cv-8": (
        "community_value",
        8.0,
        "Code, split, or protocol card is in the submission and matches the experiment.",
        "代码、划分或协议说明在投稿里，并且与实验一致。",
    ),
    "cv-10": (
        "community_value",
        10.0,
        "The release is a benchmark or tool others can run without the authors.",
        "发布物是别人可以不依赖作者就运行的基准或工具。",
    ),
    "pw-2": (
        "prior_work_contextualization",
        2.0,
        "The decision comparison's prior work is missing.",
        "决定性比较所依赖的已有工作缺失。",
    ),
    "pw-4": (
        "prior_work_contextualization",
        4.0,
        "Prior work is named and not used as the comparison the claim depends on.",
        "点名了已有工作，但没有把它用作主张所依赖的比较。",
    ),
    "pw-6": (
        "prior_work_contextualization",
        6.0,
        "The right neighbors are cited; one of them is overstated or under-compared.",
        "相关工作点名正确；其中一项被说重了或比较不足。",
    ),
    "pw-8": (
        "prior_work_contextualization",
        8.0,
        "Matched-setting neighbors are the comparison the claim uses.",
        "匹配设定的相关工作就是主张所使用的比较。",
    ),
    "pw-10": (
        "prior_work_contextualization",
        10.0,
        "Every decision comparison names task, data, and metric against a real neighbor.",
        "每个决定性比较都对真实的相关工作写明了任务、数据和指标。",
    ),
}


def clause_record(clause_id: str) -> tuple[str, float, str, str]:
    try:
        return CLAUSES[clause_id]
    except KeyError as exc:
        raise ValueError(f"unknown rubric clause {clause_id}") from exc


def clause_dimension(clause_id: str) -> str:
    return clause_record(clause_id)[0]


def clause_score(clause_id: str) -> float:
    return clause_record(clause_id)[1]


def rubric_fingerprint() -> str:
    """Stable hash of the clause table. A rubric edit busts the rate-stage cache."""
    import json

    from .hashing import digest_excerpt

    payload = {
        clause_id: [dimension, score]
        for clause_id, (dimension, score, _english, _chinese) in sorted(CLAUSES.items())
    }
    return digest_excerpt(json.dumps(payload, sort_keys=True, separators=(",", ":")))
