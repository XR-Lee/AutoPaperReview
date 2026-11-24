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
    ReviewPackage,
)

_SENTENCE = re.compile(r"(?<=[.!?。？！])\s+|(?<=\n)")
_CLAIM_HINT = re.compile(
    r"(?:\d+\s*%|achieves|generalizes|ready|concludes|outperforms|significant|state-of-the-art)",
    re.IGNORECASE,
)


PENDING_CONTRIBUTION_RISK_EN = (
    "Listed contribution: in-paper evidence and a matched-setting "
    "comparator have not been checked for this item."
)
PENDING_CONTRIBUTION_RISK_ZH = "该条列出的贡献尚未核对其文中证据与 matched-setting 对照工作。"


def _localized(text: str) -> LocalizedText:
    return LocalizedText(primary=text.strip(), language="en")


def _pending_contribution_risk() -> LocalizedText:
    return LocalizedText(
        primary=PENDING_CONTRIBUTION_RISK_EN,
        language="en",
        translations={"zh-Hans": PENDING_CONTRIBUTION_RISK_ZH},
    )


def _contribution_markers(claim: LedgerClaim) -> list[str]:
    index = claim.metadata.get("index", claim.id)
    return [str(claim.id).lower(), f"contribution {index}", f"contribution ({index})"]


def covering_contribution_issues(package: ReviewPackage, claim: LedgerClaim) -> list:
    markers = _contribution_markers(claim)
    hits = []
    for issue in package.issues:
        blob = " ".join(
            [
                issue.location,
                issue.title.primary,
                issue.evidence.primary,
                " ".join(issue.tags),
                str(issue.metadata.get("contribution_id") or ""),
            ]
        ).lower()
        if any(marker in blob for marker in markers):
            hits.append(issue)
    preferred = [
        issue
        for issue in hits
        if "M7" in issue.route_ids or issue.category == "contribution_audit"
    ]
    return preferred or hits


def apply_contribution_audits(package: ReviewPackage) -> ReviewPackage:
    """Replace pending C_i risks with the M7 audit once covering issues exist."""
    updated: list[LedgerClaim] = []
    changed = False
    for claim in package.ledger_claims:
        if claim.metadata.get("kind") != "listed_contribution":
            updated.append(claim)
            continue
        issues = covering_contribution_issues(package, claim)
        if not issues:
            updated.append(claim)
            continue
        issue = issues[0]
        evidence_ids = list(dict.fromkeys([*claim.in_paper_evidence_ids, *issue.evidence_ids]))
        metadata = {
            **claim.metadata,
            "audit": "checked",
            "audit_issue_id": issue.id,
            "note_code": "audited",
        }
        updated.append(
            claim.model_copy(
                update={
                    "risk": issue.evidence,
                    "in_paper_evidence_ids": evidence_ids,
                    "metadata": metadata,
                }
            )
        )
        changed = True
    if not changed:
        return package
    return package.model_copy(update={"ledger_claims": updated})


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
                    risk=_pending_contribution_risk(),
                    anchor=_anchor(manuscript_text, body, manuscript_sha256),
                    metadata={
                        "kind": "listed_contribution",
                        "index": index,
                        "audit": "pending",
                        "note_code": "unchecked",
                    },
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


def _rewrite_ledger_references(package: ReviewPackage, definitions: dict[str, dict[str, str]]) -> ReviewPackage:
    """Rewrite typed references in a package, leaving free-form metadata and text untouched."""
    data = package.model_dump()
    claim_ids = definitions["ledger_claims"]
    evidence_ids = definitions["evidence"]
    references = {
        "claim_ids": claim_ids,
        "in_paper_evidence_ids": evidence_ids,
        "evidence_ids": evidence_ids,
        "excluded_evidence_ids": evidence_ids,
        "decision_basis_evidence_ids": evidence_ids,
    }
    for field, records in data.items():
        if field == "overall_score" and records:
            records = [records]
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            if field in definitions and record["id"] in definitions[field]:
                old = record["id"]
                record["id"] = definitions[field][old]
                record["metadata"]["consensus_model_local_id"] = old
            if field == "novelty_assessments":
                record["claim_id"] = claim_ids.get(record["claim_id"], record["claim_id"])
            for key, mapping in references.items():
                if key in record:
                    record[key] = [mapping.get(value, value) for value in record[key]]
    return ReviewPackage.model_validate(data)


def resolve_model_ledger_ids(packages: list[ReviewPackage]) -> list[ReviewPackage]:
    """Give colliding, independently produced model ledger records distinct consensus IDs."""
    fields = ("ledger_claims", "evidence", "agenda")
    occupied = {field: set() for field in fields}
    prior = {field: {} for field in fields}
    extracted_claims = set()
    extracted_evidence = set()
    extracted_agenda = set()
    listed_contributions = set()
    for package in packages:
        for field in fields:
            for row in getattr(package, field):
                occupied[field].add(row.id)
                local = row.metadata.get("consensus_model_local_id")
                if isinstance(local, str) and prior[field].setdefault(local, row.id) != row.id:
                    raise ValueError(f"inconsistent prior consensus IDs for {field} record {local}")
        manuscript_ids = {row.id for row in package.evidence if row.source_id == "S-manuscript"}
        for claim in package.ledger_claims:
            bound = manuscript_ids.intersection(claim.in_paper_evidence_ids)
            if bound and not claim.metadata.get("judgment"):
                extracted_claims.add(claim.id)
                extracted_evidence.update(bound)
                if claim.metadata.get("kind") == "listed_contribution":
                    listed_contributions.add(claim.id)
        for question in package.agenda:
            if not question.metadata.get("judgment") and (
                "source_claim" in question.metadata or "contribution_id" in question.metadata
                or question.metadata.get("fallback")
            ):
                extracted_agenda.add(question.id)

    collisions = []
    for package in packages:
        claims = [
            row for row in package.ledger_claims if row.metadata.get("judgment") in {"contributions", "ledger"}
        ]
        bound = {eid for claim in claims for eid in claim.in_paper_evidence_ids}
        collisions.append({
            # The model's contribution audit intentionally fills the extracted numbered contribution.
            "ledger_claims": {
                row.id for row in claims if row.id in prior["ledger_claims"] or (
                    row.id in extracted_claims and not (
                        row.metadata.get("judgment") == "contributions" and row.id in listed_contributions
                    )
                )
            },
            "evidence": {
                row.id for row in package.evidence
                if row.id in bound and row.source_id != "S-manuscript"
                and (row.id in extracted_evidence or row.id in prior["evidence"])
            },
            "agenda": {
                row.id for row in package.agenda if row.metadata.get("judgment") == "ledger"
                and (row.id in extracted_agenda or row.id in prior["agenda"])
            },
        })

    aliases = {}
    for field in fields:
        # Reuse a previous consensus ID when combining it with its original input.
        mapping = prior[field].copy()
        for old in sorted({identifier for ids in collisions for identifier in ids[field]}):
            if old in mapping:
                continue
            new = f"model-{old}"
            while new in occupied[field]:
                new = f"model-{new}"
            mapping[old] = new
            occupied[field].add(new)
        aliases[field] = mapping

    resolved = []
    for package, ids in zip(packages, collisions):
        if any(ids.values()):
            definitions = {field: {key: aliases[field][key] for key in keys} for field, keys in ids.items()}
            package = _rewrite_ledger_references(package, definitions)
        resolved.append(package)
    return resolved
