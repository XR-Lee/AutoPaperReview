Produce a SAR-shaped review that remains bound to AutoPaperReview evidence records.

Required sections:

1. Summary — two to four sentences describing the paper's claim and method.
2. Strengths — concrete positives.
3. Weaknesses — concrete, actionable problems.
4. Questions — items that would change the assessment if answered.
5. Dimension scores — one numeric score on a 0–10 scale for each of: originality, importance_of_research_question, claims_supported, experimental_soundness, writing_clarity, community_value, prior_work_contextualization. Each score needs a short rationale.
6. Related-work queries — at least one query for baselines, one for the same problem, and one for related techniques.

Rules:

- Every summary, strength, weakness, question, and dimension rationale must cite evidence IDs that already exist or that you create as EvidenceRecord entries.
- Do not invent a raw overall 0–10. If an overall score is requested, it must carry explicit provenance (who scored, method, prompt/model hashes, and whether a named coefficient set was used). This repository does not ship a fitted ICLR regression.
- Distinguish abstract-only snapshots from full-text summaries. Do not treat a search snippet as final evidence.
- Return schema-valid ReviewClaim, DimensionScore, RelatedWorkQuery, and ReviewIssue records. Do not return free-form prose as the only result.
