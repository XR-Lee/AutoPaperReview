"""Anchored 2/4/6/8/10 clause table for the seven SAR dimensions.

`sar_bands_v2.md` is the prompt-facing copy. This module is the validator.
A score may omit a clause. Once a clause id is set, the score must equal it.
Band 6 needs an observable that belongs to band 6 and an opposing observable
from band 8. The clause sentences are the previous table.
"""

from __future__ import annotations

COEFFICIENT_SET = "apr-iclr-cap-v2"
RUBRIC_ID = "sar_bands_v2"
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


# Observable tokens per band. Supporting evidence for a chosen band must cite
# one of that band's tokens. Band 6 opposing evidence must cite a band-8 token.
BAND_OBSERVABLES: dict[str, dict[int, frozenset[str]]] = {
    "claims_supported": {
        2: frozenset({"table_contradicts_headline"}),
        4: frozenset({"proxy_or_different_question", "weaker_own_baseline"}),
        6: frozenset({"qualified_main_claim", "secondary_overclaim"}),
        8: frozenset({"memorable_sentence_matches_comparison", "supported_negative_result"}),
        10: frozenset({"claim_fixed_before_numbers", "uncertainty_on_claim"}),
    },
    "experimental_soundness": {
        2: frozenset({"not_an_experiment", "leakage_decides_result"}),
        4: frozenset({"named_confound", "unfair_baseline_on_decision"}),
        6: frozenset({"ablations_present", "uncertainty_missing", "copied_external_number"}),
        8: frozenset({"matched_controls_in_stack", "uncertainty_reported"}),
        10: frozenset({"protocol_card_released"}),
    },
    "originality": {
        2: frozenset({"no_step_beyond_citation"}),
        4: frozenset({"module_swap"}),
        6: frozenset({"existing_pieces_combined"}),
        8: frozenset({"new_task_or_control"}),
        10: frozenset({"problem_definition_reusable"}),
    },
    "importance_of_research_question": {
        2: frozenset({"no_stated_user"}),
        4: frozenset({"too_small_to_change_practice"}),
        6: frozenset({"community_would_use"}),
        8: frozenset({"benchmark_ranking_or_protocol"}),
        10: frozenset({"blocks_a_line_and_measurable"}),
    },
    "writing_clarity": {
        2: frozenset({"protocol_not_rebuildable", "lead_display_contradicts"}),
        4: frozenset({"lead_display_broken"}),
        6: frozenset({"one_display_easy_to_misread"}),
        8: frozenset({"decision_comparison_in_main_text"}),
        10: frozenset({"display_notation_limitations_agree"}),
    },
    "community_value": {
        2: frozenset({"nothing_reusable"}),
        4: frozenset({"release_only_promised"}),
        6: frozenset({"repeat_recipe_in_text"}),
        8: frozenset({"artifact_matches_experiment"}),
        10: frozenset({"runnable_benchmark_or_tool"}),
    },
    "prior_work_contextualization": {
        2: frozenset({"decision_prior_missing"}),
        4: frozenset({"named_not_in_table"}),
        6: frozenset({"neighbor_wrong_setting"}),
        8: frozenset({"neighbors_are_the_rows"}),
        10: frozenset({"task_data_metric_named"}),
    },
}


def band_case_errors(
    *,
    dimension: str,
    score: float,
    supporting: object,
    opposing: object,
    move_up: object,
    move_down: object,
    source_text: str = "",
) -> list[str]:
    """Reject a band that has no evidence, and flag a band-6 default.

    Supporting items must cite an observable of the chosen band. Opposing
    items must cite an observable of a different band. A 6 whose opposition
    does not cite a band-8 observable is a default middle score.
    """
    label = dimension
    errors: list[str] = []
    bands = BAND_OBSERVABLES.get(dimension)
    if bands is None:
        return [f"{label}: unknown dimension"]
    band = int(score)
    if band not in bands or float(score) != float(band):
        return [f"{label}: score {score} is not a rubric band"]
    known = set().union(*bands.values())

    def _items(value: object, role: str) -> list[dict[str, str]]:
        if not isinstance(value, list) or not value:
            errors.append(f"{label}: {role} evidence is required before the band")
            return []
        parsed: list[dict[str, str]] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                errors.append(f"{label}: {role}[{index}] must be an object")
                continue
            excerpt = str(item.get("excerpt") or "").strip()
            location = str(item.get("location") or "").strip()
            observable = str(item.get("observable") or "").strip()
            if len(excerpt) < 12:
                errors.append(f"{label}: {role}[{index}] excerpt is too short")
            if len(location) < 2:
                errors.append(f"{label}: {role}[{index}] location is required")
            if observable not in known:
                errors.append(f"{label}: {role}[{index}] observable {observable!r} is not in this dimension")
            if source_text and excerpt and excerpt not in source_text:
                errors.append(f"{label}: {role}[{index}] excerpt is not in the manuscript")
            parsed.append({"excerpt": excerpt, "location": location, "observable": observable})
        return parsed

    support = _items(supporting, "supporting")
    oppose = _items(opposing, "opposing")
    support_excerpts = {item["excerpt"] for item in support if item["excerpt"]}
    oppose_excerpts = {item["excerpt"] for item in oppose if item["excerpt"]}
    if support_excerpts and oppose_excerpts and support_excerpts & oppose_excerpts:
        errors.append(f"{label}: supporting and opposing excerpts must be different passages")
    up = str(move_up or "").strip()
    down = str(move_down or "").strip()
    if len(up) < 40 or len(down) < 40 or up == down:
        errors.append(f"{label}: move_up and move_down must be distinct statements of at least 40 characters")
    support_obs = {item["observable"] for item in support}
    oppose_obs = {item["observable"] for item in oppose}
    if support and not (support_obs & bands[band]):
        errors.append(f"{label}: supporting evidence does not cite an observable of band {band}")
    other = set().union(*(tokens for level, tokens in bands.items() if level != band))
    if oppose and not (oppose_obs & other):
        errors.append(f"{label}: opposing evidence does not cite an observable of a different band")
    if band == 6 and oppose and not (oppose_obs & bands[8]):
        errors.append(
            f"{label}: band6_default: opposing evidence does not cite a missing band-8 observable"
        )
    return errors


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
        clause_id: [RUBRIC_ID, dimension, score, english]
        for clause_id, (dimension, score, english, _chinese) in sorted(CLAUSES.items())
    }
    return digest_excerpt(json.dumps(payload, sort_keys=True, separators=(",", ":")))
