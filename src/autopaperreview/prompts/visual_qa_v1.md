Inspect every rendered manuscript page at readable scale. Check clipping, overlap, missing glyphs, orphaned captions, broken tables, metadata inconsistencies, highlights, tracked changes, affiliations, correspondence markers, and page-number references.

This route is layout QA. For scientific reading of plots, diagrams, and photos (axes, legends, overlays, whether the ink supports the citing sentence), use figure_grounded_v1.

First-read writing: describe what a first-time reader sees on the page before you name the defect. Cite the page. Fill `FirstReadNote` on issues.

Visual findings must cite page numbers and must not be used to infer scientific facts that are not visible on the page.

Page limit, US Letter, margins, anonymity metadata, font embedding, and blank caption pages are the deterministic `format_check` stage against `profiles/iclr2027.json`. Do not re-score those as a free 1–10. A failure becomes a `FormatCheck` (`format_desk_reject` or a `writing_clarity` clause ceiling). A multimodal finding must carry `page_image_sha256` and a `visible_mark` that is not the caption. Those findings change the venue rating only by setting `fatal_class` or `clause_id`.
