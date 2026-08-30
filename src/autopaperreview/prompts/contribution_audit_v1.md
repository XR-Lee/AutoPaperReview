Audit each author-listed contribution separately. Typical papers state 3–4. Do not write one novelty or evidence paragraph for the whole manuscript.

Confirm the official list from the PDF Contributions paragraph. Extracted candidates in the prompt packet may be garbled by two-column layout; the PDF list is canonical.

For each listed contribution C_i:

1. Restate it in one sentence, using the paper's numbering.
2. Evidence completeness: name the table, figure, or experiment that is supposed to test THIS claim. If the cited result tests a weaker proxy, say so. If nothing in the paper tests it, say so.
3. Targeted prior work: name the closest matched-setting comparator (same task, dataset, metric) for THIS claim, cited or uncited. A related-work paragraph for the whole paper is not a comparator for C_i.
4. Missing evidence: the smallest experiment, ablation, or citation that would make C_i auditable.

Rules:

- One issue (or an explicit “evidence complete” note bound to evidence IDs) per listed contribution.
- Location must include `Contribution N`.
- Do not treat a teaser figure or a robot clip as support for a quantitative contribution unless the caption and protocol actually measure that contribution.
- Return schema-valid review issues. Do not return free-form prose as the only result.
