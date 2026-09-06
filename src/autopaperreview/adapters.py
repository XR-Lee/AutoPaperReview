"""Optional review-execution adapters. The core does not call a model API."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .hashing import digest_excerpt
from .integrity import reporting_is_present
from .migration import load_review_input
from .models import (
    Anchor,
    AnchorKind,
    Artifact,
    CommentIntent,
    EvidenceRecord,
    FirstReadNote,
    LocalizedText,
    ReviewIssue,
    ReviewPackage,
    ReviewRoute,
    Severity,
    SourceRecord,
)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    manuscript: Artifact
    source_text: str = ""
    packets: dict[str, str] = Field(default_factory=dict)
    literature: ReviewPackage | None = None
    ledger: ReviewPackage | None = None
    fixture_path: str | None = None
    routes: list[ReviewRoute] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: ReviewPackage
    raw_artifact_names: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewAdapter(Protocol):
    def execute(self, request: ReviewRequest, scratch: Path) -> ReviewResult:
        """Return a schema-valid package. Write raw responses into scratch."""


def load_review_adapter(spec: str) -> ReviewAdapter:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not attribute:
        raise ValueError("review adapter must be a module:attribute reference")
    module = importlib.import_module(module_name)
    adapter = getattr(module, attribute)
    if isinstance(adapter, type):
        adapter = adapter()
    if not hasattr(adapter, "execute"):
        raise TypeError(f"review adapter {spec!r} does not provide execute()")
    return adapter


def _merge_literature(package: ReviewPackage, literature: ReviewPackage | None) -> ReviewPackage:
    if literature is None:
        return package
    sources = {item.id: item for item in package.sources}
    evidence = {item.id: item for item in package.evidence}
    queries = {item.id: item for item in package.related_work_queries}
    snapshots = {item.id: item for item in package.retrieved_snapshots}
    for item in literature.sources:
        sources.setdefault(item.id, item)
    for item in literature.evidence:
        evidence.setdefault(item.id, item)
    for item in literature.related_work_queries:
        queries.setdefault(item.id, item)
    for item in literature.retrieved_snapshots:
        snapshots.setdefault(item.id, item)
    return package.model_copy(
        update={
            "sources": list(sources.values()),
            "evidence": list(evidence.values()),
            "related_work_queries": list(queries.values()),
            "retrieved_snapshots": list(snapshots.values()),
        }
    )


def _merge_ledger(package: ReviewPackage, ledger: ReviewPackage | None) -> ReviewPackage:
    if ledger is None:
        return package
    claims = {item.id: item for item in package.ledger_claims}
    for item in ledger.ledger_claims:
        claims.setdefault(item.id, item)
    agenda = {item.id: item for item in package.agenda}
    for item in ledger.agenda:
        agenda.setdefault(item.id, item)
    sources = {item.id: item for item in package.sources}
    evidence = {item.id: item for item in package.evidence}
    for item in ledger.sources:
        sources.setdefault(item.id, item)
    for item in ledger.evidence:
        evidence.setdefault(item.id, item)
    return package.model_copy(
        update={
            "sources": list(sources.values()),
            "evidence": list(evidence.values()),
            "ledger_claims": list(claims.values()),
            "agenda": list(agenda.values()),
            "residual_risks": package.residual_risks or ledger.residual_risks,
        }
    )


class FixtureReviewAdapter:
    """Load a local review JSON fixture. Used by the synthetic example."""

    def execute(self, request: ReviewRequest, scratch: Path) -> ReviewResult:
        if not request.fixture_path:
            raise ValueError("FixtureReviewAdapter requires fixture_path")
        path = Path(request.fixture_path)
        raw_copy = scratch / "raw_review.json"
        raw_copy.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        package = load_review_input(path, manuscript=request.manuscript, project_id=request.project_id)
        if request.routes:
            known = {route.id: route for route in package.routes}
            for route in request.routes:
                known.setdefault(route.id, route)
            package = package.model_copy(update={"routes": list(known.values())})
        package = _merge_literature(package, request.literature)
        package = _merge_ledger(package, request.ledger)
        return ReviewResult(
            package=package,
            raw_artifact_names=["raw_review.json"],
            metadata={"adapter": "fixture", "packet_count": len(request.packets)},
        )


def _bilingual(english: str, chinese: str) -> LocalizedText:
    return LocalizedText(primary=english, language="en", translations={"zh-Hans": chinese})


def _first_read(
    *,
    paper_said_en: str,
    paper_said_zh: str,
    explanation_en: str,
    explanation_zh: str,
    quote: str | None = None,
    intent: CommentIntent = CommentIntent.issue,
    blocking: bool | None = None,
) -> FirstReadNote:
    return FirstReadNote(
        paper_said=_bilingual(paper_said_en, paper_said_zh),
        explanation=_bilingual(explanation_en, explanation_zh),
        quote=quote,
        intent=intent,
        blocking=blocking,
    )


class DeterministicManuscriptAdapter:
    """Produce a schema-valid review from local manuscript text. No model call."""

    def execute(self, request: ReviewRequest, scratch: Path) -> ReviewResult:
        text = request.source_text
        (scratch / "raw_review.json").write_text(
            '{"adapter":"deterministic-manuscript","model":null}\n',
            encoding="utf-8",
        )
        source = SourceRecord(
            id="S1",
            kind="manuscript",
            title="Manuscript",
            locator="manuscript",
            sha256=request.manuscript.sha256,
        )
        excerpt = text.strip()[:240] or "empty manuscript"
        evidence = EvidenceRecord(
            id="E1",
            source_id="S1",
            locator="manuscript",
            claim=excerpt,
            excerpt=excerpt,
            excerpt_hash=digest_excerpt(excerpt),
            artifact_sha256=request.manuscript.sha256,
            anchor=Anchor(
                kind=AnchorKind.text_offset if excerpt in text else AnchorKind.display,
                display="manuscript",
                artifact_sha256=request.manuscript.sha256,
                encoding="utf-8",
                start=text.find(excerpt) if excerpt in text else None,
                end=(text.find(excerpt) + len(excerpt)) if excerpt in text else None,
                excerpt=excerpt,
            ),
        )
        issues: list[ReviewIssue] = []
        lowered = text.lower()
        if "replace this file" in lowered or "synthetic manuscript placeholder" in lowered:
            issues.append(
                ReviewIssue(
                    id="I-placeholder",
                    severity=Severity.editorial,
                    category="completeness",
                    title=_bilingual("Placeholder manuscript has not been replaced", "尚未替换占位稿件"),
                    location="manuscript",
                    evidence=_bilingual(
                        "The init template still contains the placeholder manuscript text.",
                        "初始化模板仍包含占位稿件文本。",
                    ),
                    impact=_bilingual("No scientific claim can be evaluated.", "无法评估科学主张。"),
                    required_action=_bilingual("Replace manuscript.txt with the document to review.", "用待审稿件替换 manuscript.txt。"),
                    confidence=1.0,
                    route_ids=[route.id for route in request.routes[:1]],
                    source_ids=["S1"],
                    evidence_ids=["E1"],
                    anchor=evidence.anchor,
                    first_read=_first_read(
                        paper_said_en="The current file is still the init placeholder, not a scientific manuscript.",
                        paper_said_zh="当前文件仍是初始化占位稿，还不是一篇科学稿件。",
                        explanation_en="A first-time reader cannot evaluate a claim, method, or result because none have been supplied yet. Replace the placeholder before treating any later finding as a review of the work.",
                        explanation_zh="第一次读稿的人还看不到主张、方法或结果，因为这些内容尚未提供。在把后续意见当成对工作的审稿之前，先替换占位稿。",
                        quote=excerpt,
                        intent=CommentIntent.note,
                        blocking=True,
                    ),
                )
            )
        if "image level" in lowered or "image-level" in lowered:
            issues.append(
                ReviewIssue(
                    id="I001",
                    severity=Severity.critical,
                    category="evaluation validity",
                    title=_bilingual(
                        "The image-level split does not establish sequence independence",
                        "图像级拆分没有建立序列独立性",
                    ),
                    location="Methods and Results",
                    evidence=_bilingual(
                        "Eight temporally related images from one specimen are randomly split into training and test images.",
                        "来自同一试件的八张时间相关图像被随机拆分到训练集和测试集。",
                    ),
                    impact=_bilingual(
                        "Near-duplicate temporal content can inflate the reported accuracy and cannot support generalization to unseen structures.",
                        "时间上高度相似的内容可能抬高准确率，无法支持对未见结构的泛化结论。",
                    ),
                    required_action=_bilingual(
                        "Use specimen- or sequence-level holdout and report results on a genuinely independent test set.",
                        "采用试件级或序列级留出，并在真正独立的测试集上报告结果。",
                    ),
                    confidence=0.99,
                    route_ids=[route.id for route in request.routes[:2]],
                    source_ids=["S1"],
                    evidence_ids=["E1"],
                    anchor=evidence.anchor,
                    first_read=_first_read(
                        paper_said_en=(
                            "The paper trains and tests on eight images taken over time from one specimen. "
                            "Six images are randomly assigned to training and two to testing. The authors then "
                            "treat high accuracy on those two images as evidence that the method will work on unseen structures."
                        ),
                        paper_said_zh=(
                            "论文用同一试件在不同时间拍摄的八张图像做训练和测试：随机把六张分到训练集、两张分到测试集，"
                            "再把这两张上的高准确率当作方法可用于未见结构的证据。"
                        ),
                        explanation_en=(
                            "A first-time reader can easily read 'image-level split' as a normal train/test split. "
                            "It is not. The eight images are temporally related and come from one specimen, so the "
                            "test images are near-duplicates of the training images. Accuracy can look high even if "
                            "the method cannot handle a new specimen. The generalization and deployment claims do "
                            "not follow from this experiment."
                        ),
                        explanation_zh=(
                            "第一次读稿的人很容易把「图像级拆分」理解成普通的训练/测试划分，但这里不是。"
                            "八张图来自同一试件且时间相关，测试图几乎是训练图的近重复。即使方法不能处理新试件，"
                            "准确率也可能显得很高。因此泛化和部署结论并不能由这个实验推出。"
                        ),
                        quote="Eight temporally related images from one specimen were randomly divided at image level.",
                        intent=CommentIntent.issue,
                        blocking=True,
                    ),
                )
            )
        if "95%" in text and not reporting_is_present(text, "confidence interval"):
            issues.append(
                ReviewIssue(
                    id="I002",
                    severity=Severity.major,
                    category="statistics",
                    title=_bilingual(
                        "The deployment claim lacks uncertainty and repeated-run evidence",
                        "部署结论缺少不确定性和重复运行证据",
                    ),
                    location="Results and Conclusion",
                    evidence=_bilingual(
                        "The manuscript reports one accuracy value without confidence intervals, repeated seeds, or an external test set.",
                        "稿件只报告一个准确率，没有置信区间、多随机种子或外部测试集。",
                    ),
                    impact=_bilingual(
                        "The magnitude and stability of the claimed performance are unknown.",
                        "所声称性能的幅度和稳定性均不明确。",
                    ),
                    required_action=_bilingual(
                        "Report repeated runs, uncertainty intervals, and an external or specimen-level test.",
                        "报告重复运行、不确定性区间以及外部或试件级测试。",
                    ),
                    confidence=0.96,
                    route_ids=[request.routes[0].id] if request.routes else [],
                    source_ids=["S1"],
                    evidence_ids=["E1"],
                    anchor=evidence.anchor,
                    first_read=_first_read(
                        paper_said_en=(
                            "The paper reports a single accuracy number (95%) on the two held-out images "
                            "and concludes the method is ready for deployment."
                        ),
                        paper_said_zh="论文只在两张留出图像上报告了一个准确率（95%），并据此认为方法已可部署。",
                        explanation_en=(
                            "One number from one split does not show whether the result is stable. A first-time "
                            "reader cannot tell if 95% would appear again with a different seed, a different pair "
                            "of images, or a new specimen. Without repeated runs, an uncertainty interval, or an "
                            "external test, the deployment claim cannot be checked."
                        ),
                        explanation_zh=(
                            "一次划分得到的一个数字不能说明结果是否稳定。第一次读稿的人无法判断换一个随机种子、"
                            "换两张图或换一个试件后是否仍是 95%。没有重复运行、不确定性区间或外部测试，部署结论无法核验。"
                        ),
                        quote="Accuracy was reported on the two held-out images.",
                        intent=CommentIntent.issue,
                        blocking=True,
                    ),
                )
            )
        if not issues:
            issues.append(
                ReviewIssue(
                    id="I-open",
                    severity=Severity.moderate,
                    category="evaluation",
                    title=_bilingual("No structured evaluation protocol was detected", "未检测到结构化评估协议"),
                    location="manuscript",
                    evidence=_bilingual(
                        "Deterministic checks did not find a split, uncertainty, or placeholder marker.",
                        "确定性检查没有发现数据拆分、不确定性或占位标记。",
                    ),
                    impact=_bilingual("The review is incomplete until a protocol is stated.", "在写明协议之前，审稿不完整。"),
                    required_action=_bilingual("State the split, metric, and uncertainty protocol.", "写明拆分、指标和不确定性协议。"),
                    confidence=0.4,
                    route_ids=[request.routes[0].id] if request.routes else [],
                    source_ids=["S1"],
                    evidence_ids=["E1"],
                    anchor=evidence.anchor,
                    first_read=_first_read(
                        paper_said_en="The manuscript does not state a data split, a named metric protocol, or an uncertainty rule.",
                        paper_said_zh="稿件没有写明数据拆分、具名指标协议或不确定性规则。",
                        explanation_en="A first-time reader cannot tell what experiment was run or what number would count as success. Until those protocol sentences exist, later performance claims cannot be interpreted.",
                        explanation_zh="第一次读稿的人无法判断做了什么实验、什么数字算成功。在写出这些协议句子之前，后续性能主张无法解释。",
                        quote=excerpt,
                        intent=CommentIntent.question,
                        blocking=None,
                    ),
                )
            )
        package = ReviewPackage(
            project_id=request.project_id,
            manuscript=request.manuscript,
            routes=list(request.routes),
            sources=[source],
            evidence=[evidence],
            issues=issues,
            metadata={"adapter": "deterministic-manuscript"},
        )
        package = _merge_literature(package, request.literature)
        package = _merge_ledger(package, request.ledger)
        return ReviewResult(
            package=package,
            raw_artifact_names=["raw_review.json"],
            metadata={"adapter": "deterministic-manuscript", "issue_count": len(issues)},
        )
