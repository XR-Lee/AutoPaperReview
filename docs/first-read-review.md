# First-read review writing

AutoPaperReview `0.1.6` treats the *reader of the review* as someone who is seeing the manuscript for the first time. That reader may be an area chair, an author, or a reviewer who has not yet internalized the paper's terms. A finding that only cites `E1` or says "the split is invalid" is not an argument for that reader.

This document records the open venue guides and open reviewers this slice copied *writing rules* from. It is not a claim that this repository matches their scores, trained models, or hosted UX.

Related documents: [Architecture](architecture.md), [Roadmap](roadmap.md), [Feature gaps versus stronger open experiments](oss-experiment-gaps.md).

## The failure mode

Before this slice, the harness already stored evidence IDs, anchors, and Evidence / Impact / Required action fields. The rendered review still read like an auditor's notebook:

- Claims were one-liners that assumed the reader already knew the experiment.
- Issues led with route IDs and SHA-256-adjacent metadata.
- Dimension names were snake_case machine keys.
- Packaged prompts asked for schema-valid records, not for a restated argument.

A first-time reader cannot reconstruct "eight temporally related images from one specimen, split at image level" from "evaluation validity" and `[E1]`.

## What open practice already requires

| Source | What to take | What not to copy |
|---|---|---|
| [ICLR 2027 Reviewer Guidelines](https://iclr.cc/Conferences/2027/ReviewerGuidelines) | Restate what the paper claims to contribute; give one or two key reasons for the recommendation; then supporting arguments; then questions; extra feedback is clearly not part of the decision. Concise beats exhaustive. | ICLR's hosted form, LLM-use policy theater, or a fitted 0–10 |
| [ACL Rolling Review](https://github.com/acl-org/aclrollingreview/blob/main/reviewing.md) | A review should say what the paper is about, name strengths and weaknesses, and give advice specific enough that a chair who did not re-read every page can still judge it | ARR's cycle, ethics track, or OpenReview invitation schema |
| [Reviewer2](https://github.com/harrywang/reviewer2) / [OpenAIReview](https://github.com/ChicagoHAI/OpenAIReview) | Progressive first-read: running summary of definitions and claims; every comment is `title` / `quote` / `explanation` | Node/Inngest, their prompt module as core |
| [Conventional Comments](https://conventionalcomments.org/) | Label intent (issue / suggestion / question / praise / note), then subject, then why and the next step. A reader should understand the comment without knowing the taxonomy | Making the label the product |
| [ReviewEval](https://github.com/madhavkrishangarg/ReviewEval) | Constructiveness = specific + feasible + enough implementation detail to act on | LLM-as-judge scores as a release gate |
| DeepReviewer 2.0 ledger | What the paper claimed, what in-paper evidence is supposed to support it, residual risk | MinerU / PASA / overlay PDF as the database |
| MARG | A clarity specialist exists because a generalist still hides jargon | GPT-4 discussion as the durable schema |
| [Google engineering review comments](https://google.github.io/eng-practices/review/reviewer/comments.html) | Explain why, not only what; suggest a concrete alternative | Code-review UX |

ICLR's own sample reviews do this: they first retell Dual-AC / the LP formulation in the reviewer's words, then argue. The sample is useful as a *shape*, not as text to reproduce.

## What shipped in 0.1.6

1. **`FirstReadNote`** on `ReviewIssue` (optional, additive). Fields: `paper_said`, `explanation`, optional `quote`, Conventional Comments `intent`, optional `blocking`. Schema version stays `1.0`.
2. **`first_read_v1.md`** — packaged writing contract. `prompt_packet` prepends it to every route packet. Specialist prompts remind the model that an evidence ID is not the argument. `init` and the synthetic example add a clarity route `M6`.
3. **Reader-facing Markdown** — how-to-read note, human dimension labels next to the machine key, inlined quotes, first-read restatement when present, evidence catalog in an appendix.
4. **Optional export-gate checks** — `require_first_read_on_major`, `require_quote_on_major`, `min_explanation_chars`. Off by default so older packages still export. The synthetic example and `init` turn the first two on.
5. **Deterministic / fixture adapters** fill `FirstReadNote` so a model-free run still demonstrates the contract.

## What this is not

- Not a live model call. The contract is in the packet; a declared adapter still has to write the records.
- Not ReviewEval scoring in core. Promptfoo / DeepEval stay external.
- Not a rewrite of ObservationRecord or dissent-preserving consensus.
- Not permission to drop evidence hashes. The first-read prose is for humans; the IDs remain the audit trail.
