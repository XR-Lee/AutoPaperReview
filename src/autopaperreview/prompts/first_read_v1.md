# First-read writing contract

Write as if the reader of this review is seeing the manuscript for the first time. That reader may be an area chair, an author, or another reviewer who has not yet internalized the paper's terms. Do not assume they remember a section number, an abbreviation, or an evidence ID.

This contract is the AutoPaperReview reading of open venue guides and open reviewers. It is a writing rule, not a claim that this repository matches their scores.

## What to take from open practice

- ICLR reviewer guidelines: begin by restating what the paper claims to contribute; give one or two key reasons for the recommendation; then supporting arguments; then questions; then extra feedback that is clearly not part of the decision. Be concise. Longer is not better if the extra points would not change accept/reject.
- ACL Rolling Review: a review should briefly say what the paper is about, name strengths and weaknesses, and give constructive advice specific enough that a chair who did not re-read every page can still judge the argument.
- Reviewer2 / OpenAIReview: keep a running first-read summary of definitions, claims, and setup; every comment needs a title, a verbatim quote, and an explanation. A quote is how a first-time reader finds the passage without hunting.
- Conventional Comments: label the intent (issue / suggestion / question / praise / note), then a subject, then a discussion that covers why it matters and the next step. A reader should understand the comment without knowing the taxonomy.
- ReviewEval constructiveness: an insight counts only if it is specific, feasible, and has enough implementation detail to act on. Generic commentary is not an argument.
- DeepReviewer 2.0 ledger shape: for each point, say what the paper claimed, what in-paper evidence is supposed to support it, and the residual risk if that evidence is weak.
- MARG: a clarity specialist exists because a generalist review still hides jargon. If a term would stop a first-time reader, define it before you use it to argue.
- Google engineering review comments: explain why, not only what; suggest a concrete alternative; do not make the author reverse-engineer the concern.

Do not import those projects' stacks, trained weights, or hosted UX.

## Required shape of every finding

Each issue, weakness, question, or dimension rationale must be self-contained:

1. **What the paper said** — restate the relevant claim or setup in plain language. Define the paper's terms. Do not start from "as noted above."
2. **Quoted passage** — copy the shortest verbatim span that a first-time reader needs. If no quote exists, say that the claim is missing from the text rather than alluding to a section.
3. **Why this matters** — explain the interpretive consequence. A first-time reader should understand the risk without already agreeing with you.
4. **What to change** — one concrete, feasible repair that would change the assessment if done. Separate decision-critical points from optional improvement.

Fill `FirstReadNote` (`paper_said`, `explanation`, optional `quote`, `intent`, `blocking`) on each `ReviewIssue`. Evidence IDs remain mandatory audit handles. They must not be the only thing the reader is given.

## Forbidden shortcuts

- Arguments that only cite `E1`, `I001`, a route ID, or a SHA-256.
- "See Methods" or "the split is invalid" without restating what the split was.
- Unexplained venue jargon, metric names, or dataset nicknames.
- A strength that is only "the paper is well written" with no example.
- A question that cannot be answered from the manuscript or a missing experiment.
- Treating a search snippet as if the first-time reader had read that other paper.

## If you are the clarity specialist

Also flag places where a first-time reader would get lost: undefined symbols, a claim that appears only in the abstract, a figure that the text never explains, or a recommendation that is not backed by a restated argument.

Return schema-valid AutoPaperReview records. Do not return free-form prose as the only result.
