"""Resolve source-audited, field-balanced statements for proposal comparisons."""

from math import isfinite

from .clarification import plan_units
from .data import digest
from .outcome_screen import units

ORDER = ('problem', 'prior_alternatives', 'mechanism', 'assumptions', 'discriminating_test', 'intended_value')


def material(item):
    score = item.get('overall')
    proposal = item.get('proposal', {})
    if not isinstance(proposal, dict) or any(v is not None and not isinstance(v, str) for v in proposal.values()):
        raise ValueError('expected proposal fields containing strings or null')
    if score is None:
        return []
    if type(score) not in (float, int) or not isfinite(score) or not 1 <= score <= 5:
        raise ValueError('overall must be a finite grade between 1 and 5')
    audit = item.get('source_audit') or {}
    if (not isinstance(audit, dict) or not isinstance(audit.get('source_units'), dict)
        or not isinstance(item.get('outcome_screen'), dict) or 'proposal' not in item['outcome_screen']
        or audit.get('proposal_sha256') != digest(proposal)
        or audit.get('source_sha256') != digest(audit.get('source_units'))):
        raise ValueError('expected matching proposal and source receipts from prioritize --include-source-audit')
    allowed = set(plan_units(proposal).values())
    fields = {k: [] for k in ORDER}
    for row in units(dict(sorted(proposal.items()))).values():
        if row['field'] in fields and row['text'] in allowed:
            fields[row['field']].append(row['text'])
    sentences = []
    for i in range(max(map(len, fields.values()))):
        for field in ORDER:
            if i < len(fields[field]) and fields[field][i] not in sentences:
                sentences.append(fields[field][i])
    return sentences[:8]
