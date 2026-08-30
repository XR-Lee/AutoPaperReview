"""Deterministic claim-evidence-risk ledger and investigation agenda."""

from __future__ import annotations

import re

from .contributions import extract_listed_contributions
from .hashing import digest_excerpt
from .literature import parse_manuscript_text
from .models import (
    AgendaQuestion,
    Anchor,
    AnchorKind,
    LedgerClaim,
    LocalizedText,
    QueryPerspective,
)

_SENTENCE = re.compile(r"(?<=[.!?。？！])\s+|(?<=\n)")
_CLAIM_HINT = re.compile(
    r"(?:\d+\s*%|achieves|generalizes|ready|concludes|outperforms|significant|state-of-the-art)",
    re.IGNORECASE,
)


def _localized(text: str) -> LocalizedText:
    return LocalizedText(primary=text.strip(), language="en")


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE.split(text) if part and part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _anchor(manuscript: str, excerpt: str, manuscript_sha256: str) -> Anchor | None:
    start = manuscript.find(excerpt)
    if start < 0:
        return Anchor(kind=AnchorKind.display, display="manuscript", excerpt=excerpt)
    return Anchor(
        kind=AnchorKind.text_offset,
        display=f"manuscript[{start}:{start + len(excerpt)}]",
        artifact_sha256=manuscript_sha256,
        encoding="utf-8",
        start=start,
        end=start + len(excerpt),
        excerpt=excerpt,
        excerpt_hash=digest_excerpt(excerpt),
    )


def build_ledger_claims(manuscript_text: str, *, manuscript_sha256: str) -> list[LedgerClaim]:
    listed = extract_listed_contributions(manuscript_text)
    if listed:
        claims: list[LedgerClaim] = []
        for item in listed:
            index = int(item["index"])
            body = str(item["text"])
            claims.append(
                LedgerClaim(
                    id=f"C{index}",
                    claim=LocalizedText(
                        primary=body,
                        language="en",
                        translations={"zh-Hans": body},
                    ),
                    risk=LocalizedText(
                        primary=(
                            "Listed contribution: in-paper evidence and a matched-setting "
                            "comparator have not been checked for this item."
                        ),
                        language="en",
                        translations={
                            "zh-Hans": "该条列出的贡献尚未核对其文中证据与 matched-setting 对照工作。"
                        },
                    ),
                    anchor=_anchor(manuscript_text, body, manuscript_sha256),
                    metadata={"kind": "listed_contribution", "index": index},
                )
            )
        return claims
    parsed = parse_manuscript_text(manuscript_text)
    claims = []
    seen: set[str] = set()
    for section in ("abstract", "methods", "body"):
        for sentence in _sentences(parsed.get(section, "")):
            if not _CLAIM_HINT.search(sentence):
                continue
            key = " ".join(sentence.lower().split())
            if key in seen:
                continue
            seen.add(key)
            index = len(claims) + 1
            claims.append(
                LedgerClaim(
                    id=f"L{index}",
                    claim=_localized(sentence),
                    risk=_localized("In-paper evidence has not yet been checked against artifacts."),
                    anchor=_anchor(manuscript_text, sentence, manuscript_sha256),
                    metadata={"section": section},
                )
            )
    if not claims and parsed["title"] != "Untitled manuscript":
        claims.append(
            LedgerClaim(
                id="L1",
                claim=_localized(parsed["title"]),
                risk=_localized("Title-only ledger; no claim-like sentence was found."),
                metadata={"section": "title"},
            )
        )
    return claims


def build_agenda(claims: list[LedgerClaim]) -> list[AgendaQuestion]:
    questions: list[AgendaQuestion] = []
    for claim in claims:
        text = claim.claim.primary
        if claim.metadata.get("kind") == "listed_contribution":
            index = claim.metadata.get("index", claim.id)
            questions.append(
                AgendaQuestion(
                    id=f"A-{claim.id}-evidence",
                    question=(
                        f"What table, figure, or experiment actually tests listed "
                        f"contribution {index}: {text}"
                    ),
                    claim_ids=[claim.id],
                    perspective=QueryPerspective.agenda,
                    requires_network=False,
                    metadata={"kind": "evidence_completeness", "contribution_id": claim.id},
                )
            )
            questions.append(
                AgendaQuestion(
                    id=f"A-{claim.id}-prior",
                    question=(
                        f"Which published method is a matched-setting comparator for "
                        f"listed contribution {index}: {text}"
                    ),
                    claim_ids=[claim.id],
                    perspective=QueryPerspective.same_problem,
                    requires_network=False,
                    metadata={"kind": "targeted_retrieval", "contribution_id": claim.id},
                )
            )
            continue
        questions.append(
            AgendaQuestion(
                id=f"A-{claim.id}",
                question=f"Which published work uses a matched setting for: {text}",
                claim_ids=[claim.id],
                perspective=QueryPerspective.agenda,
                requires_network=False,
                metadata={"source_claim": claim.id},
            )
        )
    if not questions:
        questions.append(
            AgendaQuestion(
                id="A-open",
                question="What prior work addresses the same problem, baselines, and related techniques?",
                perspective=QueryPerspective.same_problem,
                metadata={"fallback": True},
            )
        )
    return questions
