# Report export

The report stage writes a review a person can paste into a form. Scores, caps, and evidence requirements are unchanged. This page is only about presentation.

## What the body contains

The default file is a short review: summary, strengths, weaknesses (each title, then the required action as its own sentence), questions, and a score justification (recommendation, venue bin, and the seven dimension scores in plain language). A short related-work note states status, gap prose, and the effective cap. Gap ids and the suggested-weakness paragraph stay in the full related-work section, which is appendix-only. A compact marker legend lists `E1`..`En`.

The body PDF is compact: no contents page, shorter heading breaks, and a footer that does not print the coefficient-set name. The appendix PDF keeps a contents page. The formula name stays in the appendix text.

The body does not include internal ids. Issue ids (`I-*`), claim ids (`C-*`), clause codes (`cs-4`), coefficient-set names, and raw evidence tokens (`E-init`) stay in the appendix. The body cites in-paper evidence only as `[E1, p.5]`.

The body also omits the evidence catalog, claim ledger, agenda, integrity notes, novelty grid, how-to-read essay, first-read quote cards, related-work query and candidate logs, and retrieved external excerpts. Those are the appendix.

```bash
autopaperreview run reviews/my-paper/review.toml
autopaperreview run reviews/my-paper/review.toml --lang zh-Hans
autopaperreview run reviews/my-paper/review.toml --lang bilingual --appendix full
```

`--lang` accepts `en`, `zh-Hans`, `bilingual`, or another BCP-47 tag (`pt-BR` is accepted as `pt-br`). The default, when the report stage sets none of `lang`, `language`, `languages`, or `bilingual`, is English even if the project `default_language` is something else. `bilingual` still writes English and Simplified Chinese section by section, and export still refuses unpaired prose.

`--appendix` is `none` (default), `summary`, or `full`. `none` writes no appendix file. `summary` and `full` write `review_appendix.md` (and `review_appendix.pdf` when reportlab is installed) beside the body. The summary is counts and ids. The full appendix is the evidence index, the `E1` = raw-id crosswalk, clause codes, query log, retrieved sources, catalog, ledger, agenda, integrity, novelty, and the detailed audit (issue cards, first-read quotes, and venue conclusions).

## Language hook

`en` renders the primary text. `zh-Hans` uses `LocalizedText` translations. Short fixture phrases are filled by the existing phrase table. Longer Chinese comes from `execute_review` with `bilingual = true`, which runs the `zh-Hans` judgment in `autopaperreview.model_exec`.

Any other tag, and Chinese the phrase table cannot fill, needs a report-stage translator:

```toml
[stages.params]
lang = "fr"
translator = "autopaperreview.model_exec:translate_package"
translator_base_url = "http://127.0.0.1:8000/v1"
translator_model = "local-reviewer"
translator_api_key_env = "APR_MODEL_API_KEY"
translator_allow_remote = false
```

`translate_package(package, language, **options) -> ReviewPackage` is the hook. A `module:attribute` function or a class with that method is accepted. The bundled hook sends one `translate` judgment through the same OpenAI-compatible client as `execute_review`. It is not called unless `translator` is set. A missing translation raises `MissingTranslationError` instead of substituting English.

## Readability

`prose_edit.py` measures the body and applies a small rewrite, then writes `readability.json` with before and after counts. The checks are the ones that change a review a reader will actually paste:

- mean and maximum sentence length, and a count of sentences over 35 words (or over 80 characters when the text is mostly CJK)
- passive-voice auxiliaries, in the style of write-good
- weasel phrases (`in order to`, `due to the fact that`, `it is important to note that`, `a large number of`, and a few siblings)
- duplicate sentences
- unexplained acronyms and very long words, with a short academic allowlist
- Flesch reading ease, using the public formula `206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)`
- presence of Summary, Strengths, Weaknesses, Questions, and Score justification (or the Chinese headings)

The rewrite substitutes those weasel phrases, splits a sentence longer than 35 words, and turns a semicolon chain into bullets. Headings, block quotes, marker-legend lines, and `[E1, p.5](#marker-e1)` citations are left alone. Citations are shielded while a sentence is split, and the paragraph is restored if a citation would be cut. Sentence metrics strip citation markup and count each bullet on its own, so a marker does not glue the rest of the review into one sentence. If a rewrite would drop a citation, or would unpair a bilingual body, the original paragraph or the original body is kept and the report says so. An LLM rewrite of prose is allowed only when scores, numbers, and evidence markers are checked and unchanged afterward. The export does not call a model by itself.

These tools were surveyed and left out:

| Tool | Why it is not a dependency |
|---|---|
| [textstat](https://github.com/textstat/textstat) | The Flesch formula above is the same calculation. A package import does not change the number. |
| [proselint](https://github.com/amperser/proselint) | A large English style corpus. Useful for essays, heavier than a review export, and it does not know citation markers. |
| [write-good](https://github.com/btford/write-good) | A Node program. The sentence-length, passive, and weasel checks used here are the same idea, in the standard library, so the export does not require Node. |
| [Vale](https://vale.sh/) | Strong for a project style guide, and it needs an external binary plus styles. That is the right tool for a docs repository, not for a one-file review body. |
| An LLM rewrite | It can change a score justification. The model path stays the explicit `translator` hook. It does not run on every export. |

## Evidence on the PDF

In-document evidence is cited in the body as `[E1, p.5]`, numbered in citation order. The page comes from the anchor, then from a text search of the submission PDF. External snapshots and retrieved abstracts are not cited in the body and are not drawn on the PDF. They appear only in the appendix. The appendix maps each display number back to the raw evidence id.

When the submission PDF sits beside the text source and the optional `annotate` extra is installed, the report stage writes `annotated_submission.pdf`: a highlight and a box on each located excerpt, a numbered label, and a one-page index. Install it with:

```bash
pip install 'autopaperreview[annotate]'
```

That extra is `pymupdf>=1.24.9,<2`. PyMuPDF 1.x is the drawing API this stage uses. The 2.0 line pulls in layout and ONNX runtime packages this export does not need, so the pin stops below 2. Without the extra, or without a sibling PDF, the body is still written and `summary.json` records `annotate.status = skipped`.

Annotated PDFs are run outputs. `*.pdf` is gitignored. Do not commit a manuscript or an annotated copy.
