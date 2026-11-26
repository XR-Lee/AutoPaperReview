# Confidentiality

This repository is public. It is a review pipeline, not a place to store reviews.

## What may be committed

- Pipeline code, tests, and generic documentation.
- Synthetic fixtures that were written for the tests. A fixture must not be copied or paraphrased from a manuscript, a review, a forum thread, or a table in a paper.

## What must stay outside the repository

Keep every real review input and output out of git. Use a local, gitignored directory such as `workdir/`, `reviews/`, `papers/`, or `uploads/`:

- Manuscripts and supplements (PDF, DOCX, TeX, extracted text, page renders).
- Review drafts, scores, ratings, and reviewer identities.
- Submission or forum identifiers, venue assignment, and author lists for a paper under review.
- Figures, tables, and numbers taken from a paper.
- Titles, abstracts, contribution sentences, and close paraphrases of any of those.

Do not "sanitize" a real paper into a fixture by changing a few words. Write a new synthetic example, or do not add a fixture.

## History

Deleting a file in a new commit does not remove it from old commits. If confidential text was pushed, do not force-push `main` without the owner's explicit go-ahead. Report the commit SHAs and ask GitHub Support to purge them after any history rewrite the owner chooses to run.

## Check

`scripts/check_confidentiality.py` scans tracked files. It fails on document and image types outside `scripts/confidentiality_allowlist.txt`, on files under the work directories above, on the patterns in `scripts/confidentiality_patterns.txt`, and on SHA-256 hashes in `scripts/confidentiality_hashes.txt`. The hash file stores digests only. It also fails on a home-directory path, on an OpenReview-shaped forum or note id in an id field, on a rating or decision stored on the same record as such an id, and on a hex digest that is the unsalted SHA-256 of another string in the same record. A synthetic id such as `synthetic-a` is not that shape. An HMAC field is not an unsalted digest. `benchmarks/fixed_eval/manifest.json` is the existing locked dev-set registry and is the one field-check exception. Home-directory paths in that file still fail. Do not add submissions to it to bypass the check.

The same check runs from `.pre-commit-config.yaml` and from `.github/workflows/confidentiality.yml`.
