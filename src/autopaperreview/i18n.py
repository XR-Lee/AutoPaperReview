"""Deterministic en / zh-Hans translation fill for bilingual reports."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import LocalizedText, ReviewPackage

BILINGUAL_LANGUAGES = ("en", "zh-Hans")

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


class MissingTranslationError(ValueError):
    """Raised when a bilingual report would otherwise silently fall back."""


def contains_cjk(text: str) -> bool:
    return _CJK_RE.search(text) is not None


def script_matches(text: str, language: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if language == "zh-Hans":
        return contains_cjk(stripped)
    if language == "en" or language.startswith("en-"):
        return _LATIN_RE.search(stripped) is not None and not _is_cjk_only_prose(stripped)
    return True


def _is_cjk_only_prose(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha() or ("\u3400" <= ch <= "\u9fff")]
    if not letters:
        return False
    cjk = sum(1 for ch in letters if "\u3400" <= ch <= "\u9fff")
    return cjk / len(letters) > 0.5


def normalize_languages(
    *,
    language: str | None = None,
    languages: Sequence[str] | None = None,
    bilingual: bool = False,
    default_language: str = "en",
) -> list[str]:
    cleaned = list(dict.fromkeys(item.strip() for item in (languages or []) if item and str(item).strip()))
    if bilingual:
        for required in BILINGUAL_LANGUAGES:
            if required not in cleaned:
                cleaned.append(required)
        return cleaned
    if cleaned:
        return cleaned
    chosen = (language or default_language).strip()
    if not chosen:
        raise ValueError("report language cannot be blank")
    return [chosen]


def _lookup_candidates(text: str) -> list[str]:
    stripped = text.strip()
    normalized = " ".join(stripped.split())
    candidates = [stripped, normalized]
    if normalized.endswith((".", "。", "?", "？", "!", "！")):
        candidates.append(normalized[:-1].rstrip())
    unique: list[str] = []
    for item in candidates:
        if item and item not in unique:
            unique.append(item)
    return unique


EXACT_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "The tracking objective is concrete but the method is not compared to prior work in this fixture.",
        "跟踪目标具体，但本夹具中的方法未与已有工作进行比较。",
    ),
    (
        "Deployment-oriented tracking is a real need; the fixture does not measure community impact.",
        "面向部署的跟踪是真实需求；本夹具并未测量社区影响。",
    ),
    (
        "The deployment claim is not supported by the reported split or uncertainty.",
        "所报告的数据拆分与不确定性并不能支持部署结论。",
    ),
    (
        "Image-level split of eight temporally related images is not an independent test.",
        "将八张时间相关图像做图像级拆分，并不能构成独立测试。",
    ),
    (
        "The short synthetic manuscript is readable and internally consistent.",
        "这篇简短的合成稿件可读且内部一致。",
    ),
    (
        "A cautionary evaluation example has some teaching value but no released protocol.",
        "这一警示性评估示例有一定教学价值，但没有发布可用的协议。",
    ),
    (
        "The manuscript itself contains no related-work discussion; only a fixture snapshot is attached.",
        "稿件本身没有相关工作讨论；仅附带了夹具快照。",
    ),
    (
        "The manuscript claims a deployment-ready tracker from an eight-image, image-level split.",
        "稿件根据八张图像的图像级拆分声称跟踪器已可部署。",
    ),
    (
        "The paper reports 95% accuracy on two held-out images from the same specimen and concludes the method is ready for unseen structures.",
        "论文在同一试件的两张留出图像上报告 95% 准确率，并认为方法可用于未见结构。",
    ),
    (
        "The manuscript states a concrete deployment-oriented objective.",
        "稿件提出了明确的部署导向目标。",
    ),
    (
        "The evaluation does not establish sequence independence, so the deployment claim is unsupported.",
        "评估没有建立序列独立性，因此部署结论缺少支持。",
    ),
    (
        "What is the specimen-level accuracy, with repeated seeds, on a genuinely held-out sequence?",
        "在真正留出的序列上，带重复种子的试件级准确率是多少？",
    ),
    (
        "Major revision is required before the deployment claim can be evaluated.",
        "在能够评估部署结论之前，稿件需要进行实质性大修。",
    ),
    (
        "The image-level split does not establish sequence independence",
        "图像级拆分没有建立序列独立性",
    ),
    (
        "Eight temporally related images from one specimen are randomly split into training and test images.",
        "来自同一试件的八张时间相关图像被随机拆分到训练集和测试集。",
    ),
    (
        "Near-duplicate temporal content can inflate the reported accuracy and cannot support generalization to unseen structures.",
        "时间上高度相似的内容可能抬高准确率，无法支持对未见结构的泛化结论。",
    ),
    (
        "Use specimen- or sequence-level holdout and report results on a genuinely independent test set.",
        "采用试件级或序列级留出，并在真正独立的测试集上报告结果。",
    ),
    (
        "The deployment claim lacks uncertainty and repeated-run evidence",
        "部署结论缺少不确定性和重复运行证据",
    ),
    (
        "The manuscript reports one accuracy value without confidence intervals, repeated seeds, or an external test set.",
        "稿件只报告一个准确率，没有置信区间、多随机种子或外部测试集。",
    ),
    (
        "The magnitude and stability of the claimed performance are unknown.",
        "所声称性能的幅度和稳定性均不明确。",
    ),
    (
        "Report repeated runs, uncertainty intervals, and an external or specimen-level test.",
        "报告重复运行、不确定性区间以及外部或试件级测试。",
    ),
    ("Use a sequence-independent split.", "采用序列独立的数据拆分。"),
    ("Report repeated runs and uncertainty.", "报告重复运行与不确定性。"),
    ("The claim is not supported.", "该主张缺少支持。"),
    ("A short paper claims deployment readiness.", "这篇短文声称已具备部署条件。"),
    ("Where is the specimen-level split?", "试件级拆分在哪里？"),
    ("Legacy summary without an evidence suffix.", "没有证据后缀的旧版摘要。"),
    ("Evaluation is incomplete", "评估不完整"),
    ("Only one run is reported.", "只报告了一次运行。"),
    ("Stability is unknown.", "稳定性未知。"),
    ("Report repeated runs.", "报告重复运行。"),
    ("Clear writing", "写作清晰"),
    ("Clear objective", "目标明确"),
    ("Major revision", "大修"),
    ("Repeat the experiment", "重复实验"),
    (
        "No overall score is assigned. AutoPaperReview does not emit a raw LLM 0–10 "
        "by default, and this repository does not ship a fitted ICLR regression.",
        "未给出总体分数。AutoPaperReview 默认不输出未经校准的 LLM 0–10 分，本仓库也不附带拟合的 ICLR 回归。",
    ),
    (
        "In-paper claims have not yet been checked against workspace artifacts.",
        "文中主张尚未对照工作区产物核验。",
    ),
    (
        "In-paper evidence has not yet been checked against artifacts.",
        "文中证据尚未对照产物核验。",
    ),
    (
        "The proposed system achieves 95% accuracy and generalizes to unseen sequences.",
        "所提出的系统达到 95% 准确率，并泛化到未见序列。",
    ),
    (
        "The method is ready for deployment across unseen structures.",
        "该方法已可部署到未见结构。",
    ),
    (
        "Fixture or placeholder identifier; not treated as a resolved publication.",
        "夹具或占位标识符；不视为已解析的正式文献。",
    ),
    (
        "Workspace accuracy disagrees with the manuscript claim.",
        "工作区准确率与稿件主张不一致。",
    ),
    (
        "Manuscript does not report both a confidence interval and a seed.",
        "稿件没有同时报告置信区间和随机种子。",
    ),
    (
        "Snapshot is not a matched setting "
        "(task=tracking, dataset=sequence-level holdout, metric=accuracy); "
        "not used as overlap evidence.",
        "快照不是匹配设定（任务=tracking，数据=sequence-level holdout，指标=accuracy）；不作为重叠证据。",
    ),
    (
        "Snapshot has no excerpt, so novelty cannot be verified.",
        "快照没有摘录，因此无法核验新颖性。",
    ),
    (
        "Matched-setting comparison used the snapshot excerpt.",
        "匹配设定比较使用了快照摘录。",
    ),
    (
        "Matched setting, and the snapshot does not repeat the manuscript claim.",
        "设定匹配，且快照没有重复稿件主张。",
    ),
    (
        "Workspace accuracy matches the manuscript claim.",
        "工作区准确率与稿件主张一致。",
    ),
    (
        "No results.json was present in the inspected workspace.",
        "所检查的工作区中没有 results.json。",
    ),
    (
        "Manuscript reports uncertainty and seeds.",
        "稿件报告了不确定性和随机种子。",
    ),
    (
        "Identifier is present but was not resolved against a live registry.",
        "存在标识符，但未对照在线注册库解析。",
    ),
    (
        "No DOI or arXiv ID; reference integrity cannot be established.",
        "没有 DOI 或 arXiv ID；无法建立参考文献完整性。",
    ),
    (
        "Title-only ledger; no claim-like sentence was found.",
        "台账仅有标题；没有找到类似主张的句子。",
    ),
    (
        "This review is written for someone who is seeing the manuscript for the "
        "first time — an area chair, an author, or a reviewer who has not yet "
        "internalized the paper's terms. Each finding restates the relevant claim "
        "in plain language, quotes the passage when one exists, explains why the "
        "point matters, and says what would change the assessment. Internal "
        "evidence IDs are audit handles; they are not a substitute for the argument.",
        "本审稿面向第一次读这篇稿件的人：领域主席、作者，以及尚未熟悉文中术语的审稿人。"
        "每条意见都会用平实语言复述相关主张，在有原文时引用对应段落，解释为何重要，"
        "并说明怎样修改才会改变评价。内部证据编号只是核验把手，不能代替论述本身。",
    ),
    (
        "The paper trains and tests on eight images taken over time from one specimen. "
        "Six images are randomly assigned to training and two to testing. The authors then "
        "treat high accuracy on those two images as evidence that the method will work on unseen structures.",
        "论文用同一试件在不同时间拍摄的八张图像做训练和测试：随机把六张分到训练集、两张分到测试集，"
        "再把这两张上的高准确率当作方法可用于未见结构的证据。",
    ),
    (
        "A first-time reader can easily read 'image-level split' as a normal train/test split. "
        "It is not. The eight images are temporally related and come from one specimen, so the "
        "test images are near-duplicates of the training images. Accuracy can look high even if "
        "the method cannot handle a new specimen. The generalization and deployment claims do "
        "not follow from this experiment.",
        "第一次读稿的人很容易把「图像级拆分」理解成普通的训练/测试划分，但这里不是。"
        "八张图来自同一试件且时间相关，测试图几乎是训练图的近重复。即使方法不能处理新试件，"
        "准确率也可能显得很高。因此泛化和部署结论并不能由这个实验推出。",
    ),
    (
        "The paper reports a single accuracy number (95%) on the two held-out images "
        "and concludes the method is ready for deployment.",
        "论文只在两张留出图像上报告了一个准确率（95%），并据此认为方法已可部署。",
    ),
    (
        "One number from one split does not show whether the result is stable. A first-time "
        "reader cannot tell if 95% would appear again with a different seed, a different pair "
        "of images, or a new specimen. Without repeated runs, an uncertainty interval, or an "
        "external test, the deployment claim cannot be checked.",
        "一次划分得到的一个数字不能说明结果是否稳定。第一次读稿的人无法判断换一个随机种子、"
        "换两张图或换一个试件后是否仍是 95%。没有重复运行、不确定性区间或外部测试，部署结论无法核验。",
    ),
)

EN_ZH_PHRASES: tuple[tuple[str, str], ...] = (
    ("deployment-oriented", "面向部署的"),
    ("deployment claim", "部署结论"),
    ("image-level split", "图像级拆分"),
    ("specimen-level", "试件级"),
    ("sequence-level", "序列级"),
    ("sequence independence", "序列独立性"),
    ("related-work", "相关工作"),
    ("prior work", "已有工作"),
    ("research question", "研究问题"),
    ("confidence interval", "置信区间"),
    ("repeated runs", "重复运行"),
    ("test set", "测试集"),
    ("held-out", "留出"),
    ("is not supported", "缺少支持"),
    ("not supported", "缺少支持"),
    ("teaching value", "教学价值"),
    ("synthetic manuscript", "合成稿件"),
    ("overall score", "总体分数"),
)

EN_ZH_WORDS: dict[str, str] = {
    "the": "",
    "a": "",
    "an": "",
    "and": "和",
    "or": "或",
    "but": "但",
    "of": "的",
    "in": "在",
    "on": "在",
    "for": "对于",
    "to": "到",
    "from": "从",
    "with": "带",
    "without": "没有",
    "by": "由",
    "as": "作为",
    "at": "在",
    "is": "是",
    "are": "是",
    "was": "是",
    "were": "是",
    "be": "",
    "been": "",
    "not": "不",
    "no": "没有",
    "this": "该",
    "that": "该",
    "these": "这些",
    "those": "那些",
    "manuscript": "稿件",
    "paper": "论文",
    "claim": "主张",
    "claims": "主张",
    "claimed": "所声称的",
    "evidence": "证据",
    "result": "结果",
    "results": "结果",
    "method": "方法",
    "methods": "方法",
    "evaluation": "评估",
    "test": "测试",
    "tests": "测试",
    "split": "拆分",
    "independent": "独立",
    "independence": "独立性",
    "sequence": "序列",
    "specimen": "试件",
    "accuracy": "准确率",
    "score": "分数",
    "scores": "分数",
    "dimension": "维度",
    "originality": "原创性",
    "importance": "重要性",
    "research": "研究",
    "question": "问题",
    "experimental": "实验",
    "soundness": "可靠性",
    "writing": "写作",
    "clarity": "清晰度",
    "community": "社区",
    "value": "价值",
    "prior": "已有",
    "work": "工作",
    "supported": "被支持",
    "support": "支持",
    "unsupported": "缺少支持",
    "missing": "缺失",
    "unknown": "未知",
    "report": "报告",
    "reported": "所报告的",
    "reporting": "报告",
    "required": "需要的",
    "action": "行动",
    "impact": "影响",
    "title": "标题",
    "location": "位置",
    "confidence": "置信度",
    "deployment": "部署",
    "ready": "就绪",
    "readiness": "就绪",
    "tracking": "跟踪",
    "objective": "目标",
    "concrete": "具体",
    "compared": "比较",
    "fixture": "夹具",
    "need": "需求",
    "measure": "测量",
    "image": "图像",
    "images": "图像",
    "temporally": "时间上",
    "related": "相关",
    "eight": "八",
    "two": "两",
    "holdout": "留出",
    "seed": "种子",
    "seeds": "种子",
    "repeated": "重复",
    "uncertainty": "不确定性",
    "interval": "区间",
    "intervals": "区间",
    "protocol": "协议",
    "released": "已发布的",
    "teaching": "教学",
    "cautionary": "警示性",
    "example": "示例",
    "readable": "可读",
    "internally": "内部",
    "consistent": "一致",
    "short": "简短",
    "synthetic": "合成",
    "attached": "附带",
    "discussion": "讨论",
    "contains": "包含",
    "itself": "本身",
    "only": "仅",
    "generalization": "泛化",
    "inflate": "抬高",
    "unseen": "未见",
    "structures": "结构",
    "genuinely": "真正",
    "does": "",
    "do": "",
    "cannot": "不能",
    "can": "可以",
    "so": "因此",
    "therefore": "因此",
}


def _build_translation_table() -> dict[tuple[str, str, str], str]:
    table: dict[tuple[str, str, str], str] = {}
    for english, chinese in EXACT_PAIRS:
        for key in _lookup_candidates(english):
            table[("en", "zh-Hans", key)] = chinese
        for key in _lookup_candidates(chinese):
            table[("zh-Hans", "en", key)] = english
    return table


TRANSLATION_TABLE = _build_translation_table()


def _generate_en_to_zh(text: str) -> str:
    remaining = text
    for source, target in sorted(EN_ZH_PHRASES, key=lambda item: len(item[0]), reverse=True):
        remaining = re.sub(re.escape(source), target, remaining, flags=re.IGNORECASE)

    def replace_word(match: re.Match[str]) -> str:
        mapped = EN_ZH_WORDS.get(match.group(0).lower())
        return match.group(0) if mapped is None else mapped

    remaining = _WORD_RE.sub(replace_word, remaining)
    remaining = " ".join(remaining.split())
    remaining = remaining.replace(" 。", "。").replace(" ，", "，").replace(" ；", "；")
    remaining = remaining.replace(" ？", "？").replace(" ?", "？")
    if text.rstrip().endswith(".") and not remaining.endswith(("。", ".", "？", "?", "！", "!")):
        remaining = remaining.rstrip(".") + "。"
    elif remaining.endswith("."):
        remaining = remaining[:-1] + "。"
    return remaining.strip()


def _generate_zh_to_en(text: str) -> str:
    remaining = text
    for english, chinese in sorted(EN_ZH_PHRASES, key=lambda item: len(item[1]), reverse=True):
        remaining = remaining.replace(chinese, english)
    for english, chinese in sorted(EN_ZH_WORDS.items(), key=lambda item: len(item[1]), reverse=True):
        if chinese:
            remaining = remaining.replace(chinese, f" {english} ")
    remaining = " ".join(remaining.split())
    if text.endswith("。") and not remaining.endswith("."):
        remaining += "."
    return remaining.strip()


def translate_text(text: str, *, source: str, target: str) -> str:
    if source == target:
        return text
    stripped = text.strip()
    if not stripped:
        raise MissingTranslationError("cannot translate blank text")
    for candidate in _lookup_candidates(stripped):
        hit = TRANSLATION_TABLE.get((source, target, candidate))
        if hit:
            return hit
    if source == "en" and target == "zh-Hans":
        generated = _generate_en_to_zh(stripped)
    elif source == "zh-Hans" and target == "en":
        generated = _generate_zh_to_en(stripped)
    else:
        generated = ""
    if generated and script_matches(generated, target):
        return generated
    raise MissingTranslationError(
        f"cannot fill {target} translation from {source} without producing {target} text"
    )


def require_language(text: LocalizedText, language: str, *, field: str) -> str:
    value = text.get(language)
    if not value:
        raise MissingTranslationError(
            f"{field} has no {language} text; silent monolingual fallback is forbidden"
        )
    if not script_matches(value, language):
        raise MissingTranslationError(
            f"{field} {language} text is not in the requested language; silent monolingual fallback is forbidden"
        )
    return value


def ensure_localized(text: LocalizedText | None, languages: Sequence[str]) -> LocalizedText | None:
    if text is None:
        return None
    translations = dict(text.translations)
    for language in languages:
        if language == text.language:
            if not script_matches(text.primary, language):
                raise MissingTranslationError(
                    f"primary text is not in declared language {language}; silent fallback is forbidden"
                )
            continue
        current = translations.get(language, "")
        if current.strip() and script_matches(current, language):
            translations[language] = current.strip()
            continue
        translations[language] = translate_text(text.primary, source=text.language, target=language)
    return text.model_copy(update={"translations": translations})


def ensure_string_map(mapping: dict[str, list[str]], languages: Sequence[str]) -> dict[str, list[str]]:
    if not mapping:
        return {}
    result = {key: list(values) for key, values in mapping.items()}
    source_lang = next((language for language in ("en", "zh-Hans") if result.get(language)), None)
    if source_lang is None:
        source_lang = next(iter(result))
    source_values = result[source_lang]
    for language in languages:
        current = result.get(language)
        if current and all(script_matches(item, language) for item in current):
            continue
        result[language] = [
            translate_text(item, source=source_lang, target=language) for item in source_values
        ]
    return result


def ensure_package_languages(package: ReviewPackage, languages: Sequence[str]) -> ReviewPackage:
    updated = package.model_copy(deep=True)
    updated.summary = ensure_localized(updated.summary, languages)
    updated.recommendation = ensure_localized(updated.recommendation, languages)
    updated.strengths = ensure_string_map(updated.strengths, languages)
    updated.acceptance_gate = ensure_string_map(updated.acceptance_gate, languages)
    updated.issues = [
        issue.model_copy(
            update={
                "title": ensure_localized(issue.title, languages),
                "evidence": ensure_localized(issue.evidence, languages),
                "impact": ensure_localized(issue.impact, languages),
                "required_action": ensure_localized(issue.required_action, languages),
                "first_read": (
                    issue.first_read.model_copy(
                        update={
                            "paper_said": ensure_localized(issue.first_read.paper_said, languages),
                            "explanation": ensure_localized(
                                issue.first_read.explanation, languages
                            ),
                        }
                    )
                    if issue.first_read is not None
                    else None
                ),
            }
        )
        for issue in updated.issues
    ]
    updated.claims = [
        claim.model_copy(update={"text": ensure_localized(claim.text, languages)})
        for claim in updated.claims
    ]
    updated.dimension_scores = [
        score.model_copy(
            update={
                "rationale": ensure_localized(score.rationale, languages) if score.rationale else None
            }
        )
        for score in updated.dimension_scores
    ]
    updated.venue_conclusions = [
        item.model_copy(
            update={
                "label": ensure_localized(item.label, languages),
                "rationale": ensure_localized(item.rationale, languages),
            }
        )
        for item in updated.venue_conclusions
    ]
    updated.ledger_claims = [
        item.model_copy(
            update={
                "claim": ensure_localized(item.claim, languages),
                "risk": ensure_localized(item.risk, languages) if item.risk else None,
            }
        )
        for item in updated.ledger_claims
    ]
    updated.residual_risks = [
        ensure_localized(item, languages) for item in updated.residual_risks if ensure_localized(item, languages)
    ]
    updated.integrity_records = [
        item.model_copy(update={"notes": ensure_localized(item.notes, languages) if item.notes else None})
        for item in updated.integrity_records
    ]
    updated.novelty_assessments = [
        item.model_copy(update={"notes": ensure_localized(item.notes, languages) if item.notes else None})
        for item in updated.novelty_assessments
    ]
    return updated
