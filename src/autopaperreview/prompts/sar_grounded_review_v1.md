Produce a SAR-shaped review that remains bound to AutoPaperReview evidence records.

Required sections:

1. Summary — two to four sentences describing the paper's claim and method.
2. Strengths — concrete positives.
3. Weaknesses — concrete, actionable problems.
4. Questions — items that would change the assessment if answered.
5. Dimension scores — one numeric score on a 1–10 scale for each of: originality, importance_of_research_question, claims_supported, experimental_soundness, writing_clarity, community_value, prior_work_contextualization. Each score needs a short rationale. Do not combine them into an overall number.
6. Venue conclusions — for conferences (at least ICLR, NeurIPS, AAAI, CVPR, RSS) and journals (at least TMLR, JMLR, TPAMI, T-RO), give a venue-specific outcome (reject / weak_reject / borderline / major_revision / minor_revision / weak_accept / accept / not_a_fit) and a short rationale. Journals may recommend revision; conferences typically accept or reject. Do not emit an overall 0–10.
7. Related-work queries — at least one query for baselines, one for the same problem, and one for related techniques.

Rules:

- Every summary, strength, weakness, question, dimension rationale, and venue conclusion must cite evidence IDs that already exist or that you create as EvidenceRecord entries.
- Do not invent a raw overall 0–10 or a fitted ICLR/AAAI coefficient mapping. The seven dimension scores stay separate; conclusions differ by venue.
- Distinguish abstract-only snapshots from full-text summaries. Do not treat a search snippet as final evidence.
- Figure-backed claims (qualitative examples, trade-off plots, robot photos, architecture cartoons) require PDF page inspection. A caption restated from manuscript.txt is not evidence that the figure was read.
- Listed contributions are the audit units. Score claims_supported, originality, and prior_work_contextualization per contribution, not as a single paper-level paragraph.
- Return schema-valid ReviewClaim, DimensionScore, RelatedWorkQuery, and ReviewIssue records. Do not return free-form prose as the only result.
