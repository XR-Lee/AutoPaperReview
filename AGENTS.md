# Agent instructions

This repository is public. Read [CONFIDENTIALITY.md](CONFIDENTIALITY.md) before adding files.

- Never commit content tied to a real paper or review. That includes titles, abstracts, contribution sentences, paraphrases, table numbers, figures, page images, PDFs, ratings, reviewer names, submission ids, forum ids, and venue assignment for a submission.
- Review inputs and outputs stay outside the repo, in a gitignored directory such as `workdir/`, `reviews/`, `papers/`, or `uploads/`.
- Tests and examples use synthetic fixtures only. Do not adapt a manuscript into a fixture.
- Do not put a leaked sentence into the denylist, a commit message, a pull request, or documentation. Exact-string blocking belongs in `scripts/confidentiality_hashes.txt` as a SHA-256 digest.
- Run `python3 scripts/check_confidentiality.py` before committing. Do not force-push `main` to hide history. Report the commits to the owner instead.
