# Stanford Agentic Reviewer vs AutoPaperReview

This is a written comparison of the hosted Stanford ML Group Agentic Reviewer (SAR; [paperreview.ai](https://paperreview.ai/) and [tech overview](https://paperreview.ai/tech-overview)) against this repository. It is not a claim that AutoPaperReview matches SAR's ICLR calibration.

## What SAR owns

SAR is a closed hosted service. The public workflow, as documented by Jiang and Ng, is:

1. PDF upload (first 15 pages) plus optional venue and an email token.
2. PDF → Markdown via LandingAI Agentic Document Extraction.
3. Multi-specificity web search queries covering baselines, the same problem, and related techniques.
4. Tavily search over arXiv, metadata download, then optional full-text summaries of the most relevant papers.
5. A review grounded in those related-work summaries.
6. Seven dimension scores: originality, importance of the research question, whether claims are supported, experimental soundness, writing clarity, community value, and prior-work contextualization.
7. A linear regression from those seven scores to an ICLR-style 0–10, fitted on 150 ICLR 2025 submissions and tested on 147. Stanford reports Spearman 0.42 versus one human reviewer, compared with human–human 0.41. AUC for predicting acceptance is 0.75 for the AI score and 0.84 for one human score. The overall score is shown on the site only when the selected venue is ICLR.

The output shape is Summary / Strengths / Weaknesses / Detailed Comments / Questions / Overall Assessment.

AutoPaperReview does **not** reproduce those Spearman or AUC numbers here. They belong to Stanford's hosted, trained mapping. This repository has not measured them.

## What AutoPaperReview owns

AutoPaperReview is a local-first evidence harness:

- Canonical Pydantic records for manuscripts, sources, evidence, issues, runs, and now optional SAR-shaped scores, claims, queries, and retrieved-source snapshots.
- A TOML DAG with hash-addressed artifacts, resume, and a deny-by-default network declaration gate.
- Prompt packets and reports that stay bound to evidence IDs.
- No core model call, no core PDF parser, and no hosted UX.

The 0.1.x slice added in this comparison's companion change:

- Schema hooks for the seven SAR dimensions, narrative claims with mandatory evidence IDs, related-work query sets, and retrieved snapshots that record query IDs, arXiv IDs, retrieval date, and abstract vs full-text summary.
- An optional overall score that is **absent by default**. If present it must declare provenance. `llm_direct` requires an explicit uncalibrated-notes field; `linear_regression` requires a named coefficient set. This repo does not ship fitted ICLR coefficients.
- A `literature_grounding` stage that generates the three SAR query perspectives from local manuscript text and can attach offline snapshot fixtures. Live retrieval is optional, declared, and never a Tavily/OpenAlex/PaperQA core dependency.

## Close cousins, cited not vendored

- [Acture/reviewloop](https://github.com/Acture/reviewloop) is a CLI/daemon around paperreview.ai. It is not imported here.
- [How Far Are We From True Auto-Research?](https://arxiv.org/abs/2605.19156) (Zhang, Wang, Galhotra, Cardie; arXiv:2605.19156) uses SAR as a manuscript-only lens and contrasts it with a 9-dimension artifact-aware review (novelty, soundness, significance, clarity, reproducibility, experimental rigor, references, reference integrity, results integrity). They find SAR is a weak acceptance discriminator: on 200 ICLR 2025 papers the human accept/reject gap is 1.52 points while SAR compresses it to 0.25, and SAR rewards polished framing without checking experimental substance. That is a reason to keep evidence hashes, snapshot provenance, and optional artifact-aware routes — not a reason to clone SAR's hosted score.

## Honest gap

| Capability | Stanford SAR | AutoPaperReview |
|---|---|---|
| Hosted PDF upload / email token / venue picker | Yes | No; out of scope |
| PDF → Markdown | LandingAI ADE | Adapter later; core does not parse PDF |
| Live arXiv-grounded search | Tavily + arXiv, hosted | Query generation + snapshot records; live retriever is an optional declared adapter |
| 7 dimension scores | Produced in the hosted agent | First-class records; values come from imported or adapter output |
| ICLR 0–10 via fitted regression | Yes, trained/tested by Stanford | Schema only; no coefficients, no claimed Spearman |
| Evidence-hash resume | Not the product | Core |
| Canonical local package | No | Core |
| Network default | Hosted outbound search | Deny unless declared |

The remaining SAR gap that this repo still does not close, by design: a hosted UX, a trained ICLR mapping, and a bundled Tavily/arXiv worker. Roadmap work after this slice remains ObservationRecord, typed locators, versioned adapter contracts, and consensus-with-dissent — not a second paperreview.ai.

For a broader comparison with DeepReviewer 2.0, ResearchArena's artifact-aware review, AgentReview, MARG, OpenReviewer, PaperQA2, and related experiments, see [feature gaps versus stronger open experiments](oss-experiment-gaps.md). That review is the source of the post-0.1.3 slice order in [Roadmap](roadmap.md).
