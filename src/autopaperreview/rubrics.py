"""Anchored 2/4/6/8/10 clause table for the seven SAR dimensions.

`sar_bands_v2.md` is the prompt-facing copy. This module is the validator.
A score may omit a clause. Once a clause id is set, the score must equal it.
Band 6 is not a default: it needs an observable that belongs to band 6 and an
opposing observable from band 8.
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
        "A table or figure in the paper contradicts the headline sentence.",
        "论文中的表或图与标题句相矛盾。",
    ),
    "cs-4": (
        "claims_supported",
        4.0,
        "The headline is a proxy, answers a different question than the display, or rests on a weaker variant the authors built.",
        "标题主张是代理、与展示答非所问，或只靠作者自建的更弱变体。",
    ),
    "cs-6": (
        "claims_supported",
        6.0,
        "The main comparison supports a qualified sentence, and a second sentence claims a new setting, significance, or a general win.",
        "主比较支持一个已限定的句子，另一句却声称新设定、显著性或普遍获胜。",
    ),
    "cs-8": (
        "claims_supported",
        8.0,
        "The sentence a reader remembers is the comparison that was run, including a supported negative result.",
        "读者会记住的句子就是所做的比较，包括得到支持的负面结果。",
    ),
    "cs-10": (
        "claims_supported",
        10.0,
        "The claim was fixed before the numbers, the setting matches, and the display includes uncertainty.",
        "主张在看到数字之前已经写定，设定匹配，且展示包含不确定性。",
    ),
    "es-2": (
        "experimental_soundness",
        2.0,
        "No stated protocol produces the number, or a leak is what creates the headline result.",
        "没有写明的协议产生该数字，或泄漏造就了标题结果。",
    ),
    "es-4": (
        "experimental_soundness",
        4.0,
        "The protocol is stated, but no ablation removes the claimed piece and moves the decision metric, and no external number is copied. A needed baseline is not rerun, the run is a single seed without an interval, an ablation misses the claim, or a confound is named.",
        "协议已写明，但没有消融去掉所声称的部件并改变决定性指标，也没有抄录外部数字。所需基线未重跑、只有单一种子且无区间、消融未触及主张，或写明了混淆。",
    ),
    "es-6": (
        "experimental_soundness",
        6.0,
        "An ablation removes the claimed piece and the decision metric moves, or one external number is copied, and a band-8 condition is still missing.",
        "消融去掉了所声称的部件且决定性指标发生变化，或抄录了一个外部数字，同时仍缺一个 8 档条件。",
    ),
    "es-8": (
        "experimental_soundness",
        8.0,
        "Matched baselines are rerun in the same stack, the decision metric has an interval or multiple seeds, and an ablation isolates the claim.",
        "匹配基线在同一代码栈重跑，决定性指标有区间或多种子，且消融分离了主张。",
    ),
    "es-10": (
        "experimental_soundness",
        10.0,
        "The es-8 conditions hold and the submission includes a protocol card or script listing seeds, split, and the decision metric.",
        "满足 es-8，且投稿包含写明种子、划分和决定性指标的协议卡或脚本。",
    ),
    "or-2": (
        "originality",
        2.0,
        "The method section adds no step that is not already in a cited method.",
        "方法部分没有已引用方法之外的步骤。",
    ),
    "or-4": (
        "originality",
        4.0,
        "The new piece is one module swapped on a public pipeline.",
        "新内容是在公开流程上更换的一个模块。",
    ),
    "or-6": (
        "originality",
        6.0,
        "Existing pieces are combined, and an ablation shows that removing one changes the decision metric, without changing what the comparison measures.",
        "组合了已有部件，消融表明去掉其一会改变决定性指标，但比较所度量的对象没变。",
    ),
    "or-8": (
        "originality",
        8.0,
        "A new task, control, or objective forces baselines to be adapted, so the comparison measures something new.",
        "新任务、对照或目标迫使基线被改造，因此比较在度量新的东西。",
    ),
    "or-10": (
        "originality",
        10.0,
        "The problem statement can be reused by someone who does not adopt the method.",
        "不采用本方法的人也可以复用这个问题陈述。",
    ),
    "im-2": (
        "importance_of_research_question",
        2.0,
        "The paper never names a person or community who would use the result.",
        "论文没有指出谁会使用这个结果。",
    ),
    "im-4": (
        "importance_of_research_question",
        4.0,
        "The task is real, and the reported result stays on a toy or synthetic set, or the paper calls the gain too small to change a decision.",
        "任务真实，但结果停在玩具或合成集上，或论文称增益小到改变不了决策。",
    ),
    "im-6": (
        "importance_of_research_question",
        6.0,
        "A reader in the area would rerun a confirmed version, and the paper does not show an existing public benchmark changing rank or protocol.",
        "领域读者会重跑确认后的版本，且论文没有表明已有公开基准的排名或协议会改变。",
    ),
    "im-8": (
        "importance_of_research_question",
        8.0,
        "If the result holds, a benchmark people already run changes its ranking or its protocol.",
        "结果若成立，人们已经在跑的基准会改变排名或协议。",
    ),
    "im-10": (
        "importance_of_research_question",
        10.0,
        "The question blocks a line of work, and the paper makes it measurable by someone else.",
        "该问题卡住一条研究线，且论文使其他人可以测量它。",
    ),
    "wc-2": (
        "writing_clarity",
        2.0,
        "A reader cannot rebuild the protocol from the text, or a lead display contradicts the text.",
        "读者无法从正文重建协议，或主展示与正文矛盾。",
    ),
    "wc-4": (
        "writing_clarity",
        4.0,
        "The protocol is rebuildable, and a lead figure or equation is broken or uses a different quantity than the text.",
        "协议可重建，但主图或公式损坏，或使用了与正文不同的量。",
    ),
    "wc-6": (
        "writing_clarity",
        6.0,
        "The decision comparison is checkable, and exactly one display is easy to misread as a stronger result.",
        "决定性比较可以核对，且恰好有一处展示容易被误读成更强的结果。",
    ),
    "wc-8": (
        "writing_clarity",
        8.0,
        "A first-time reader can check the decision comparison from the main text, and no display overstates it.",
        "第一次阅读的人可以从正文核对决定性比较，且没有展示夸大它。",
    ),
    "wc-10": (
        "writing_clarity",
        10.0,
        "The display, the notation, and the limitations use the same claim.",
        "展示、符号和局限性使用同一个主张。",
    ),
    "cv-2": (
        "community_value",
        2.0,
        "The paper states nothing another group could reuse.",
        "论文没有写出另一组人可以复用的内容。",
    ),
    "cv-4": (
        "community_value",
        4.0,
        "A split, protocol, or negative result would be reusable and is only promised, not specified.",
        "划分、协议或负面结果本可复用，但只作承诺，没有写明。",
    ),
    "cv-6": (
        "community_value",
        6.0,
        "The paper writes a split, hyperparameters, or a negative finding with enough detail to repeat, and attaches no archive.",
        "论文写明了足以重复的划分、超参或负面发现，但没有附上存档。",
    ),
    "cv-8": (
        "community_value",
        8.0,
        "Code, a split, or a protocol card is in the submission and matches the experiment that was run.",
        "代码、划分或协议卡在投稿中，且与所做实验一致。",
    ),
    "cv-10": (
        "community_value",
        10.0,
        "The release is a benchmark or tool another group can run without the authors.",
        "发布物是另一组人可以不依赖作者运行的基准或工具。",
    ),
    "pw-2": (
        "prior_work_contextualization",
        2.0,
        "The method the decision comparison depends on is neither cited nor used as a baseline.",
        "决定性比较所依赖的方法既未引用也未用作基线。",
    ),
    "pw-4": (
        "prior_work_contextualization",
        4.0,
        "Neighbors are named and are not rows in the decision table the claim uses.",
        "点名了相关工作，但它们不是主张所用决策表中的行。",
    ),
    "pw-6": (
        "prior_work_contextualization",
        6.0,
        "The decision table includes the relevant neighbors, and one of them is the wrong setting or is only cited.",
        "决策表包含相关工作，但其中一项设定不对或只被引用。",
    ),
    "pw-8": (
        "prior_work_contextualization",
        8.0,
        "The matched-setting neighbors are the rows the claim's comparison uses.",
        "匹配设定的相关工作就是主张比较所用的那些行。",
    ),
    "pw-10": (
        "prior_work_contextualization",
        10.0,
        "Every decision comparison states task, data, and metric against a real neighbor.",
        "每个决定性比较都对真实相关工作写明了任务、数据和指标。",
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
        4: frozenset({"needed_baseline_not_rerun", "single_seed_no_interval", "ablation_misses_claim", "named_confound"}),
        6: frozenset({"ablation_moves_decision_metric", "copied_external_number"}),
        8: frozenset({"baselines_rerun_in_stack", "interval_or_multiseed", "ablation_isolates_claim"}),
        10: frozenset({"protocol_card_released"}),
    },
    "originality": {
        2: frozenset({"no_step_beyond_citation"}),
        4: frozenset({"module_swap"}),
        6: frozenset({"combination_ablation_hurts"}),
        8: frozenset({"new_task_or_control"}),
        10: frozenset({"problem_definition_reusable"}),
    },
    "importance_of_research_question": {
        2: frozenset({"no_stated_user"}),
        4: frozenset({"toy_or_marginal"}),
        6: frozenset({"reader_would_rerun"}),
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
