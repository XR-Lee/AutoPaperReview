"""Synthetic related-work search. No real manuscripts or real paper titles."""

from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path

from tests.confirmation_fakes import FakeConfirmationAdapter, RecordingModelSession

from autopaperreview.bilingual_coverage import require_bilingual_markdown
from autopaperreview.export_gate import ExportGateParams, export_gate_errors
from autopaperreview.pipeline import PipelineError, run_pipeline
from autopaperreview.models import (
    DimensionScore,
    EvidenceRecord,
    FatalClass,
    ReviewPackage,
    SarDimension,
    SourceRecord,
)
from autopaperreview.rating import rate_venue
from autopaperreview.readability import first_read_gate_errors
from autopaperreview.reporting import render_markdown
from autopaperreview.related_work import (
    MANUSCRIPT_BASELINE_EVIDENCE_ID,
    BaselineGap,
    CapSignal,
    HttpResult,
    RelatedCandidate,
    RetrievalFailure,
    RetrievalLogEntry,
    allow_tokens,
    assert_outbound_safe,
    MAX_MUST_COMPARE,
    RETRIEVAL_CACHE_TTL_SECONDS,
    S2_UNAUTH_BUDGET_SECONDS,
    build_cap_signals,
    classify_date,
    clear_retrieval_cache,
    derive_formulation,
    evidence_id_for_candidate,
    _section,
    generate_search_queries,
    is_cited,
    issues_from_cap_signals,
    invoke_confirmation_adapter,
    named_methods_from_manuscript,
    provider_request_headers,
    _search_url,
    parse_experiments,
    load_venue_deadlines,
    manuscript_baseline_excerpt,
    manuscript_baseline_records,
    parse_bibliography,
    preview_venue_bin,
    render_related_work_section,
    run_related_work_search,
)
from tests.support import make_artifact
from autopaperreview.venues import load_venue_profile

EXAMPLE = Path("examples/synthetic/related_work")
MANUSCRIPT = (EXAMPLE / "manuscript.txt").read_text(encoding="utf-8")


def _synthetic_problem() -> str:
    return textwrap.dedent(
        """\
        Title: Synthetic Specimen Tracker

        Problem
        Task: monocular specimen pose estimation
        Inputs: monocular RGB frames
        Outputs: a 6-DoF pose trajectory
        Assumptions: rigid specimen
        Setting: offline evaluation with a sequence-level holdout and pose error in millimeters
        Technique: temporal fusion
        """
    ).rstrip()


def _wrapped_aaai_bibliography() -> str:
    """34 numeric AAAI-style entries. [24] is the hyphenated closest method."""
    lines: list[str] = []
    for index in range(1, 35):
        if index == 24:
            lines.append(
                "[24] A. Synthetic and B. Author. Foo-BAR: Temporal fusion for\n"
                "specimen pose. In Proceedings of the Synthetic Conference,\n"
                "2021. arXiv:0000.00024."
            )
        elif index == 1:
            lines.append(
                "1. A. Author. Filler Note 01. In Proceedings of the Synthetic Conference, "
                "2019. arXiv:0000.00001."
            )
        elif index == 5:
            lines.append(
                "[5] C. Writer and D. Writer. Filler Note About\n"
                "Registration Methods.\n"
                "2018."
            )
        elif index % 3 == 0:
            lines.append(
                f"[{index}] A. Author and B. Author. Filler Note {index:02d}.\n"
                f"{2010 + (index % 9)}."
            )
        elif index % 3 == 1:
            lines.append(
                f"{index}. A. Author. Filler Note {index:02d}. "
                "In Proceedings of the Synthetic Conference, 2019."
            )
        else:
            lines.append(
                f"({index}) A. Author and B. Author. Filler Note {index:02d}. Proc. Synthetic, 2017."
            )
    return "\n".join(lines)


def _bands(**overrides: float) -> dict[str, float]:
    base = {
        "experimental_soundness": 8.0,
        "claims_supported": 8.0,
        "originality": 8.0,
        "importance_of_research_question": 8.0,
        "prior_work_contextualization": 8.0,
        "writing_clarity": 8.0,
        "community_value": 8.0,
    }
    base.update(overrides)
    return base


class FormulationAndQueryTests(unittest.TestCase):
    def test_formulation_reads_task_inputs_outputs_assumptions_and_setting(self) -> None:
        form = derive_formulation(MANUSCRIPT)
        self.assertTrue(form.complete)
        self.assertEqual(form.task, "monocular specimen pose estimation")
        self.assertEqual(form.inputs, "monocular RGB frames")
        self.assertEqual(form.outputs, "a 6-DoF pose trajectory")
        self.assertEqual(form.assumptions, ["rigid specimen", "known camera intrinsics"])
        self.assertIn("sequence-level holdout", form.setting)
        self.assertEqual(form.extra_modality, "depth")
        self.assertEqual(len(form.contributions), 3)
        self.assertTrue(form.contributions[0].startswith("We define"))

    def test_queries_cover_five_families_and_omit_manuscript_text(self) -> None:
        form = derive_formulation(MANUSCRIPT)
        queries = generate_search_queries(form)
        families = [query.metadata["family"] for query in queries]
        self.assertEqual(
            families,
            [
                "same_problem",
                "same_technique",
                "alternative_formulation",
                "standard_baselines",
                "adjacent_tasks",
            ],
        )
        blob = " ".join(query.query for query in queries).lower()
        self.assertNotIn("zephyrcodename", blob)
        self.assertNotIn("zephyrbench", blob)
        self.assertNotIn("this abstract sentence", blob)
        self.assertIn("depth", next(query.query for query in queries if query.metadata["family"] == "alternative_formulation"))
        self.assertIn("baseline", next(query.query for query in queries if query.metadata["family"] == "standard_baselines"))
        self.assertIn("survey", next(query.query for query in queries if query.metadata["family"] == "adjacent_tasks"))

    def test_outbound_guard_refuses_an_abstract_sentence(self) -> None:
        form = derive_formulation(MANUSCRIPT)
        allow = allow_tokens(form)
        with self.assertRaisesRegex(RuntimeError, "manuscript text"):
            assert_outbound_safe("We introduce ZephyrCodename a tracker", MANUSCRIPT, allow)

    def test_incomplete_formulation_does_not_search_or_cap(self) -> None:
        text = "Title: Untitled note\n\nAbstract\nA short note without a problem statement.\n"
        judgment = run_related_work_search(text, mode="agent", allow_network=False)
        self.assertFalse(judgment.formulation.complete)
        self.assertEqual(judgment.queries, [])
        self.assertEqual(judgment.candidates, [])
        self.assertEqual(judgment.cap_signals, [])
        self.assertEqual(judgment.status, "not_run")
        self.assertIn("not supported yet", judgment.weakness_en)


class BibliographyDeadlineTests(unittest.TestCase):
    def test_bibliography_parses_titles_doi_and_arxiv(self) -> None:
        entries = parse_bibliography(MANUSCRIPT)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].title, "A Public Baseline for Synthetic Pose Estimation")
        self.assertEqual(entries[0].doi, "10.0000/synthetic.pose.2022")
        self.assertEqual(entries[1].arxiv_id, "0000.00002")
        self.assertEqual(entries[1].year, 2023)

    def test_wrapped_numeric_and_author_year_references_keep_their_titles(self) -> None:
        text = f"{_synthetic_problem()}\n\nReferences\n{_wrapped_aaai_bibliography()}\n"
        entries = parse_bibliography(text)
        self.assertEqual([entry.marker for entry in entries], [str(index) for index in range(1, 35)])
        closest = next(entry for entry in entries if entry.marker == "24")
        self.assertEqual(closest.title, "Foo-BAR: Temporal fusion for specimen pose")
        self.assertEqual(closest.arxiv_id, "0000.00024")
        self.assertEqual(closest.year, 2021)
        self.assertTrue(all(entry.title and "Filler Note" in entry.title for entry in entries if entry.marker != "24"))
        self.assertTrue(all(entry.title != "2018" for entry in entries))

        author_year = f"""{_synthetic_problem()}

Related Work
Smith and Jones 2021 estimate pose. Smith et al. 2021 agree.

Experiments
Lee et al. 2022 report a depth number.

Method
Name and Other 2020 is the nearest description.

References
Smith, A. and Jones, B. 2021. Temporal Fusion Pose
Estimator. In Proceedings of the Synthetic Conference.

Lee, C. et al.
2022.
Depth Channel Survey. Journal of Synthetic Vision.

Name and Other. 2020. Held Out Pose Fusion.
Proc. Synthetic.

[8]
2019.
"""
        parsed = parse_bibliography(author_year)
        by_marker = {entry.marker: entry for entry in parsed}
        self.assertEqual(by_marker["smith-2021"].title, "Temporal Fusion Pose Estimator")
        self.assertEqual(by_marker["lee-2022"].title, "Depth Channel Survey")
        self.assertEqual(by_marker["lee-2022"].year, 2022)
        self.assertEqual(by_marker["name-2020"].title, "Held Out Pose Fusion")
        self.assertIsNone(by_marker["8"].title)
        queries = run_related_work_search(author_year, mode="off").queries
        cited = [
            query
            for query in queries
            if query.metadata.get("family") == "cited_reference" and query.metadata.get("lookup") == "title"
        ]
        self.assertTrue(all(query.metadata["cited_in_body"] for query in cited))
        self.assertEqual(
            [query.metadata["marker"] for query in cited if query.metadata["providers"] == ["arxiv"]],
            ["smith-2021", "lee-2022", "name-2020"],
        )
        self.assertNotIn("8", {query.metadata["marker"] for query in cited})

        ranged = f"""{_synthetic_problem()}

Related Work
See [3, 24] and [20–25].

References
""" + "\n".join(
            f"[{index}] A. Author. Range Note {index:02d}. In Proceedings, 2019." for index in range(1, 27)
        )
        ranged_queries = run_related_work_search(ranged, mode="off").queries
        cited_markers = {
            query.metadata["marker"]
            for query in ranged_queries
            if query.metadata.get("family") == "cited_reference" and query.metadata.get("cited_in_body")
        }
        self.assertEqual(cited_markers, {"3", "20", "21", "22", "23", "24", "25"})

    def test_arxiv_ids_are_batched_and_untitled_entries_do_not_use_slots(self) -> None:
        bibliography = "\n".join(
            f"[{index}] A. Author. Filler Note {index:02d}. In Proceedings, 2019. arXiv:0000.{index:05d}."
            for index in range(1, 42)
        )
        text = f"{_synthetic_problem()}\n\nReferences\n{bibliography}\n"
        judgment = run_related_work_search(text, mode="off", bibliography_lookup_limit=60)
        batches = [query.query for query in judgment.queries if query.metadata.get("lookup") == "arxiv_id_list"]
        self.assertEqual([len(batch.split(",")) for batch in batches], [40, 1])
        url = _search_url("arxiv", batches[0], 5, lookup="arxiv_id_list")
        self.assertIn("id_list=", url)
        self.assertIn("max_results=40", url)
        self.assertNotIn("search_query=", url)

        skipped = f"""{_synthetic_problem()}

Related Work
Only [1] is cited.

References
[1] A. Author. Real Title Here. In Proceedings, 2019.
[2]
2019.
[3] 2020.
"""
        skipped_queries = run_related_work_search(skipped, mode="off", bibliography_lookup_limit=5).queries
        markers = {
            query.metadata["marker"]
            for query in skipped_queries
            if query.metadata.get("family") == "cited_reference"
        }
        self.assertEqual(markers, {"1"})

    def test_dates_after_the_submission_deadline_are_concurrent(self) -> None:
        deadline = date(2026, 9, 25)
        self.assertEqual(classify_date("2026-09-25", 2026, deadline), "prior")
        self.assertEqual(classify_date("2026-09-26", 2026, deadline), "concurrent")
        self.assertEqual(classify_date(None, 2026, deadline), "unknown")
        self.assertEqual(classify_date(None, 2025, deadline), "prior")

    def test_venue_profiles_pin_public_deadlines(self) -> None:
        iclr = load_venue_deadlines("iclr2027")
        aaai = load_venue_deadlines("aaai2027")
        self.assertEqual(iclr.abstract_deadline.isoformat(), "2026-09-18")
        self.assertEqual(iclr.submission_deadline.isoformat(), "2026-09-25")
        self.assertEqual(aaai.abstract_deadline.isoformat(), "2026-07-21")
        self.assertEqual(aaai.submission_deadline.isoformat(), "2026-07-28")
        self.assertEqual(aaai.deadline_timezone, "Anywhere on Earth")
        self.assertIn("iclr.cc", iclr.source)
        self.assertIn("aaai.org", aaai.source)

    def test_deadline_fields_merge_onto_a_format_profile_without_replacing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy(Path("src/autopaperreview/profiles/iclr2027.deadlines.json"), root / "iclr2027.deadlines.json")
            (root / "iclr2027.json").write_text(
                json.dumps({"id": "iclr-2027-submission", "max_content_pages": 9, "anonymous": True}),
                encoding="utf-8",
            )
            merged = load_venue_profile("iclr2027", directory=root)
            self.assertEqual(merged["max_content_pages"], 9)
            self.assertTrue(merged["anonymous"])
            self.assertEqual(merged["submission_deadline"], "2026-09-25")
            self.assertEqual(merged["abstract_deadline"], "2026-09-18")
            self.assertEqual(merged["id"], "iclr-2027-submission")


def _confirm_must_compare(candidate):
    return True, "Same task and temporal fusion on a sequence-level holdout."


class AgentAndLiveTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_retrieval_cache()
    def test_agent_drop_verifies_ids_and_drops_invented_papers(self) -> None:
        with self.assertLogs("autopaperreview.related_work", level="WARNING") as logs:
            judgment = run_related_work_search(
                MANUSCRIPT,
                venue_key="iclr2027",
                mode="agent",
                findings_path=EXAMPLE / "findings.json",
                allow_network=False,
            )
        warning = "\n".join(logs.output)
        self.assertIn("Ghost Paper That Does Not Exist", warning)
        self.assertIn("not resolved", warning)
        self.assertIn("does not match", warning)
        titles = {item.title for item in judgment.candidates}
        self.assertNotIn("Ghost Paper That Does Not Exist", titles)
        self.assertNotIn("Not The Resolved Title", titles)
        self.assertNotIn("Resolved Synthetic Note", titles)
        by_title = {item.title: item for item in judgment.candidates}
        public = by_title["A Public Baseline for Synthetic Pose Estimation"]
        depth = by_title["Depth Aware Pose Estimator"]
        adjacent = by_title["Adjacent Camera Localization Survey"]
        concurrent = by_title["Concurrent Pose Note"]
        self.assertEqual(public.tier, "must-compare")
        self.assertTrue(public.required)
        self.assertTrue(public.cited)
        self.assertEqual(public.venue, "Synthetic Vision Workshop")
        self.assertEqual(public.year, 2022)
        self.assertEqual(public.external_id, "doi:10.0000/synthetic.pose.2022")
        self.assertTrue(public.link.startswith("https://doi.org/"))
        self.assertEqual(depth.tier, "must-compare")
        self.assertTrue(depth.cited)
        self.assertTrue(depth.confirmed)
        self.assertIn("temporal fusion", depth.reason_en.lower())
        self.assertNotIn("already accepts", depth.reason_en.lower())
        self.assertEqual(depth.external_id, "arxiv:0000.00002")
        self.assertEqual(adjacent.tier, "should-discuss")
        self.assertFalse(adjacent.required)
        self.assertIn("survey", adjacent.reason_en.lower())
        self.assertFalse(adjacent.cited)
        self.assertEqual(concurrent.date_relation, "concurrent")
        self.assertFalse(concurrent.required)
        self.assertNotEqual(concurrent.tier, "must-compare")
        self.assertIn("concurrent", concurrent.reason_en.lower())
        missing = {gap.title for gap in judgment.gaps if gap.kind == "missing_baseline"}
        self.assertIn(public.title, missing)
        self.assertIn(depth.title, missing)
        self.assertTrue(any(gap.kind == "privileged_baseline" for gap in judgment.gaps))
        self.assertTrue(any(gap.kind == "different_task" for gap in judgment.gaps))
        classes = {signal.fatal_class: signal for signal in judgment.cap_signals}
        self.assertEqual(classes["unfair_baseline"].venue_cap, 5)
        self.assertEqual(classes["unfair_baseline"].dimension_ceilings["experimental_soundness"], 4.0)
        self.assertEqual(classes["missing_closest_neighbor"].venue_cap, 6)
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, classes["unfair_baseline"].evidence_ids)
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, classes["missing_closest_neighbor"].evidence_ids)
        self.assertIn(evidence_id_for_candidate(public.id), classes["missing_closest_neighbor"].evidence_ids)
        self.assertIn(evidence_id_for_candidate(depth.id), classes["missing_closest_neighbor"].evidence_ids)
        self.assertNotIn(concurrent.id, classes["missing_closest_neighbor"].candidate_ids)
        self.assertNotIn(
            evidence_id_for_candidate(concurrent.id),
            classes["missing_closest_neighbor"].evidence_ids,
        )
        self.assertEqual(judgment.effective_venue_cap, 5)
        clauses = {item.clause_id for item in judgment.clause_advice}
        self.assertIn("pw-2", clauses)
        self.assertIn("es-4", clauses)
        self.assertIn("or-4", clauses)
        self.assertIn("Scale Oracle", judgment.weakness_en)
        self.assertIn("ground-truth scale", judgment.weakness_en.lower())
        self.assertIn("2026-09-25", judgment.rebuttal_en)
        self.assertTrue(judgment.weakness_zh)
        self.assertTrue(judgment.rebuttal_zh)
        self.assertEqual(judgment.status, "complete")

    def test_partial_outage_caps_a_verified_neighbor_and_never_sends_manuscript_text(self) -> None:
        seen: list[str] = []
        atom = textwrap.dedent(
            """\
            <?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>http://arxiv.org/abs/0000.00001v1</id>
                <published>2022-06-01T00:00:00Z</published>
                <title>A Public Baseline for Synthetic Pose Estimation</title>
                <summary>Offline temporal fusion estimates a 6-DoF pose trajectory for monocular specimen pose estimation. Pose error in millimeters is measured on a sequence-level holdout.</summary>
              </entry>
            </feed>
            """
        ).encode("utf-8")

        def transport(url: str) -> HttpResult:
            seen.append(url)
            if "export.arxiv.org" in url:
                return HttpResult(200, atom)
            if "openalex.org" in url:
                return HttpResult(429, b"slow down")
            raise RetrievalFailure("offline", "name resolution failed")

        with self.assertLogs("autopaperreview.related_work", level="WARNING") as logs:
            judgment = run_related_work_search(
                MANUSCRIPT,
                mode="live",
                allow_network=True,
                transport=transport,
                providers=["arxiv", "openalex", "semantic_scholar"],
                max_results=3,
                confirm_must_compare=_confirm_must_compare,
                sleeper=lambda _seconds: None,
            )
        blob = "\n".join(seen).lower()
        self.assertNotIn("zephyrcodename", blob)
        self.assertNotIn("zephyrbench", blob)
        self.assertNotIn("this abstract sentence", blob)
        self.assertTrue(any("export.arxiv.org" in url for url in seen))
        warning = "\n".join(logs.output)
        self.assertIn("rate_limited", warning)
        self.assertIn("offline", warning)
        self.assertEqual(judgment.status, "partial")
        self.assertEqual(judgment.failed_providers, ["openalex", "semantic_scholar"])
        self.assertNotIn("arxiv", judgment.failed_providers)
        verified = next(item for item in judgment.candidates if item.title.startswith("A Public Baseline"))
        self.assertEqual(verified.provider, "arxiv")
        self.assertTrue(verified.required)
        self.assertTrue(verified.confirmed)
        self.assertEqual(verified.date_relation, "prior")
        classes = {signal.fatal_class: signal for signal in judgment.cap_signals}
        self.assertEqual(set(classes), {"missing_closest_neighbor", "unfair_baseline"})
        missing = classes["missing_closest_neighbor"]
        self.assertEqual(missing.venue_cap, 6)
        self.assertEqual(missing.candidate_ids, [verified.id])
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, missing.evidence_ids)
        self.assertIn(evidence_id_for_candidate(verified.id), missing.evidence_ids)
        self.assertEqual(classes["unfair_baseline"].venue_cap, 5)
        self.assertEqual(classes["unfair_baseline"].candidate_ids, [])
        self.assertEqual(judgment.effective_venue_cap, 5)
        self.assertTrue(any(item.cited for item in judgment.candidates))
        report = "\n".join(render_related_work_section(judgment, ["en", "zh-Hans"]))
        self.assertIn("Status: partial.", report)
        self.assertIn("Failed providers: openalex, semantic_scholar.", report)
        self.assertIn("The search may be incomplete.", report)
        self.assertIn("失败的数据源：openalex, semantic_scholar。", report)
        self.assertIn("检索可能不完整。", report)
        issues = issues_from_cap_signals(
            judgment,
            anchor_excerpt=manuscript_baseline_excerpt(MANUSCRIPT),
        )
        self.assertCountEqual(
            [issue.fatal_class for issue in issues],
            [FatalClass.missing_closest_neighbor, FatalClass.unfair_baseline],
        )
        missing_issue = next(issue for issue in issues if issue.fatal_class == FatalClass.missing_closest_neighbor)
        self.assertEqual(missing_issue.metadata["candidate_providers"], ["arxiv"])
        self.assertIsNotNone(missing_issue.first_read)
        self.assertTrue(missing_issue.first_read.paper_said.translations.get("zh-Hans"))
        self.assertNotIn("\n", manuscript_baseline_excerpt(MANUSCRIPT))

    def test_timeout_and_zero_providers_emit_no_cap(self) -> None:
        def timeout(url: str) -> HttpResult:
            raise RetrievalFailure("timeout", "timed out")

        timed = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=timeout,
            providers=["arxiv"],
        )
        self.assertEqual(timed.status, "degraded")
        self.assertEqual(timed.failed_providers, ["arxiv"])
        self.assertEqual(timed.cap_signals, [])
        self.assertIsNone(timed.effective_venue_cap)
        self.assertTrue(any(entry.status == "timeout" for entry in timed.logs))
        timed_report = "\n".join(render_related_work_section(timed, ["en"]))
        self.assertNotIn("may be incomplete", timed_report)

        def unused(url: str) -> HttpResult:
            raise AssertionError(url)

        empty = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=unused,
            providers=[],
        )
        self.assertEqual(empty.status, "degraded")
        self.assertEqual(empty.failed_providers, [])
        self.assertEqual(empty.cap_signals, [])
        self.assertTrue(any("zero providers" in entry.detail for entry in empty.logs))

    def test_live_mode_without_network_is_refused_before_transport(self) -> None:
        def transport(url: str) -> HttpResult:
            raise AssertionError(url)

        with self.assertRaisesRegex(PermissionError, "metadata network"):
            run_related_work_search(MANUSCRIPT, mode="live", allow_network=False, transport=transport)


class CapSeparationTests(unittest.TestCase):
    def _signal(self, fatal_class: str, cap: int, ceilings: dict[str, float]) -> CapSignal:
        return CapSignal(
            id=f"CAP-{fatal_class}",
            fatal_class=fatal_class,  # type: ignore[arg-type]
            venue_cap=cap,
            dimension_ceilings=ceilings,
            clause_ids={},
            reason_en="synthetic",
            reason_zh="合成样例",
        )

    def test_preview_bins_still_separate_papers(self) -> None:
        missing = self._signal(
            "missing_closest_neighbor",
            6,
            {"prior_work_contextualization": 2.0, "originality": 4.0},
        )
        unfair = self._signal("unfair_baseline", 5, {"experimental_soundness": 4.0})
        self.assertEqual(preview_venue_bin(_bands(), []), 8)
        self.assertEqual(preview_venue_bin({name: 10.0 for name in _bands()}, []), 10)
        self.assertEqual(preview_venue_bin(_bands(), [missing]), 6)
        self.assertEqual(preview_venue_bin(_bands(), [unfair]), 5)
        self.assertEqual(preview_venue_bin(_bands(), [missing, unfair]), 5)
        low = _bands(experimental_soundness=2.0)
        self.assertEqual(preview_venue_bin(low, [missing]), 3)
        self.assertNotEqual(preview_venue_bin(_bands(), [missing]), preview_venue_bin(low, [missing]))
        self.assertNotEqual(missing.venue_cap, unfair.venue_cap)


class CapWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_retrieval_cache()
    def _scored_package(self, judgment, issues):
        source, baseline = manuscript_baseline_records(
            MANUSCRIPT,
            sha256=None,
            locator="manuscript.txt",
        )
        sources = [source]
        evidence = [baseline]
        for candidate in judgment.candidates:
            source_id = f"S-{candidate.id}"
            sources.append(
                SourceRecord(
                    id=source_id,
                    kind="retrieved-abstract",
                    title=candidate.title,
                    locator=candidate.link,
                )
            )
            evidence.append(
                EvidenceRecord(
                    id=evidence_id_for_candidate(candidate.id),
                    source_id=source_id,
                    locator=candidate.link,
                    claim=candidate.summary,
                    excerpt=candidate.summary,
                    metadata={
                        "candidate_id": candidate.id,
                        "external_id": candidate.external_id,
                        "venue": candidate.venue,
                        "year": candidate.year,
                        "published": candidate.published,
                        "date_relation": candidate.date_relation,
                        "verified": True,
                    },
                )
            )
        scores = [
            DimensionScore(
                dimension=dimension,
                score=8,
                evidence_ids=[MANUSCRIPT_BASELINE_EVIDENCE_ID],
            )
            for dimension in SarDimension
        ]
        return ReviewPackage(
            project_id="synthetic-caps",
            manuscript=make_artifact(),
            sources=sources,
            evidence=evidence,
            issues=issues,
            dimension_scores=scores,
            metadata={"related_work": judgment.model_dump(mode="json")},
        )

    def test_completed_search_caps_cite_evidence_and_lower_the_bin(self) -> None:
        judgment = run_related_work_search(
            MANUSCRIPT,
            venue_key="iclr2027",
            mode="agent",
            findings_path=EXAMPLE / "findings.json",
            allow_network=False,
        )
        issues = issues_from_cap_signals(
            judgment,
            anchor_excerpt=manuscript_baseline_excerpt(MANUSCRIPT),
        )
        by_class = {issue.fatal_class: issue for issue in issues}
        self.assertEqual(set(by_class), {FatalClass.missing_closest_neighbor, FatalClass.unfair_baseline})
        missing = by_class[FatalClass.missing_closest_neighbor]
        unfair = by_class[FatalClass.unfair_baseline]
        self.assertTrue(missing.decision_relevant)
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, missing.evidence_ids)
        self.assertEqual(unfair.metadata["manuscript_anchor"], MANUSCRIPT_BASELINE_EVIDENCE_ID)
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, unfair.evidence_ids)
        self.assertIsNotNone(unfair.anchor)
        self.assertIn("ground-truth scale", (unfair.anchor.excerpt or "").lower())
        self.assertIn("Scale Oracle", unfair.anchor.excerpt or "")
        self.assertGreaterEqual(len(missing.evidence_ids), 2)
        self.assertIsNotNone(missing.anchor)
        self.assertIn("Compared baselines", missing.anchor.excerpt or "")
        package = self._scored_package(judgment, issues)
        evidence = {item.id: item for item in package.evidence}
        for evidence_id in missing.evidence_ids:
            if evidence_id == MANUSCRIPT_BASELINE_EVIDENCE_ID:
                self.assertEqual(evidence[evidence_id].metadata["role"], "manuscript_baselines")
                self.assertIsNotNone(evidence[evidence_id].anchor)
                continue
            record = evidence[evidence_id]
            self.assertTrue(record.metadata["verified"])
            self.assertEqual(record.metadata["date_relation"], "prior")
            self.assertTrue(record.metadata["external_id"])
        scored = rate_venue(package)
        self.assertEqual(scored.value, 5)
        self.assertCountEqual(scored.metadata["cap_ids"], ["CAP-missing-neighbor", "CAP-unfair-baseline"])
        self.assertEqual(scored.metadata["related_work_comparison"]["status"], "complete")
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, scored.evidence_ids)
        self.assertTrue(any(item.startswith("E-RW-") for item in scored.evidence_ids))
        bands = scored.metadata["bands_after_ceiling"]
        self.assertEqual(bands["experimental_soundness"], 4.0)
        self.assertEqual(bands["originality"], 4.0)
        self.assertEqual(bands["prior_work_contextualization"], 2.0)

    def test_degraded_search_does_not_lower_the_score(self) -> None:
        def offline(url: str) -> HttpResult:
            raise RetrievalFailure("offline", "name resolution failed")

        judgment = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=offline,
            providers=["arxiv", "openalex"],
        )
        self.assertEqual(judgment.status, "degraded")
        excerpt = manuscript_baseline_excerpt(MANUSCRIPT)
        self.assertEqual(issues_from_cap_signals(judgment, anchor_excerpt=excerpt), [])
        injected = issues_from_cap_signals(
            judgment.model_copy(update={"status": "complete", "cap_signals": []}),
            anchor_excerpt=excerpt,
        )
        self.assertEqual(injected, [])
        from tests.test_rating import _all, _fatal, _package

        issue = _fatal("CAP-unfair-baseline", FatalClass.unfair_baseline)
        issue.metadata = {"source": "related_work_search"}
        package = _package("degraded", _all(8), issues=[issue])
        package.metadata["related_work"] = judgment.model_dump(mode="json")
        scored = rate_venue(package)
        self.assertEqual(scored.value, 8)
        self.assertEqual(scored.metadata["cap_ids"], [])
        self.assertEqual(scored.metadata["related_work_comparison"]["status"], "degraded")
        self.assertEqual(scored.metadata["related_work_comparison"]["failed_providers"], ["arxiv", "openalex"])

    def test_unfair_baseline_cites_a_manuscript_anchor_without_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            findings = Path(temporary) / "findings.json"
            findings.write_text('{"candidates": []}\n', encoding="utf-8")
            judgment = run_related_work_search(
                MANUSCRIPT,
                mode="agent",
                findings_path=findings,
                allow_network=False,
            )
        self.assertEqual(judgment.status, "complete")
        self.assertEqual(judgment.candidates, [])
        self.assertEqual(judgment.failed_providers, [])
        self.assertEqual([signal.fatal_class for signal in judgment.cap_signals], ["unfair_baseline"])
        signal = judgment.cap_signals[0]
        self.assertEqual(signal.venue_cap, 5)
        self.assertEqual(signal.candidate_ids, [])
        self.assertEqual(signal.evidence_ids, [MANUSCRIPT_BASELINE_EVIDENCE_ID])
        excerpt = manuscript_baseline_excerpt(MANUSCRIPT)
        self.assertIn("ground-truth scale", excerpt.lower())
        self.assertIn("Scale Oracle", excerpt)
        self.assertIn("image classification", excerpt.lower())
        issues = issues_from_cap_signals(judgment, anchor_excerpt=excerpt)
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue.fatal_class, FatalClass.unfair_baseline)
        self.assertEqual(issue.evidence_ids, [MANUSCRIPT_BASELINE_EVIDENCE_ID])
        self.assertEqual(issue.metadata["candidate_ids"], [])
        self.assertEqual(issue.metadata["manuscript_anchor"], MANUSCRIPT_BASELINE_EVIDENCE_ID)
        self.assertIsNotNone(issue.anchor)
        self.assertEqual(issue.anchor.display, "Experiments / References")
        self.assertEqual(issue.anchor.excerpt, excerpt)
        self.assertEqual(issues_from_cap_signals(judgment, anchor_excerpt=""), [])
        self.assertEqual(
            issues_from_cap_signals(
                judgment,
                anchor_excerpt="No labeled baseline or related-work lines were found.",
            ),
            [],
        )
        unanchored = judgment.model_copy(
            update={
                "cap_signals": [signal.model_copy(update={"evidence_ids": []})],
            }
        )
        self.assertEqual(issues_from_cap_signals(unanchored, anchor_excerpt=excerpt), [])
        source, evidence = manuscript_baseline_records(
            MANUSCRIPT,
            sha256=None,
            locator="manuscript.txt",
        )
        self.assertEqual(evidence.id, MANUSCRIPT_BASELINE_EVIDENCE_ID)
        self.assertEqual(evidence.source_id, source.id)
        self.assertIsNotNone(evidence.anchor)
        self.assertEqual(evidence.anchor.excerpt, excerpt)
        self.assertIn("Scale Oracle", evidence.excerpt or "")
        package = self._scored_package(judgment, issues)
        resolved = next(item for item in package.evidence if item.id == MANUSCRIPT_BASELINE_EVIDENCE_ID)
        self.assertEqual(resolved.anchor.excerpt, issue.anchor.excerpt)
        self.assertEqual(resolved.metadata["role"], "manuscript_baselines")
        self.assertIn("ground-truth scale", (resolved.anchor.excerpt or "").lower())
        scored = rate_venue(package)
        self.assertEqual(scored.value, 5)
        self.assertEqual(scored.metadata["cap_ids"], ["CAP-unfair-baseline"])
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, scored.evidence_ids)
        self.assertFalse(any(item.startswith("E-RW-") for item in scored.evidence_ids))
        self.assertEqual(scored.metadata["bands_after_ceiling"]["experimental_soundness"], 4.0)

    def test_partial_search_caps_only_a_verified_missing_neighbor(self) -> None:
        def transport(url: str) -> HttpResult:
            if "export.arxiv.org" in url:
                return HttpResult(200, _pose_atom(published="2022-06-01T00:00:00Z"))
            raise RetrievalFailure("offline", "name resolution failed")

        judgment = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=transport,
            providers=["arxiv", "openalex"],
            confirm_must_compare=_confirm_must_compare,
            sleeper=lambda _seconds: None,
        )
        self.assertEqual(judgment.status, "partial")
        self.assertEqual(judgment.failed_providers, ["openalex"])
        issues = issues_from_cap_signals(
            judgment,
            anchor_excerpt=manuscript_baseline_excerpt(MANUSCRIPT),
        )
        self.assertCountEqual(
            [issue.fatal_class for issue in issues],
            [FatalClass.missing_closest_neighbor, FatalClass.unfair_baseline],
        )
        package = self._scored_package(judgment, issues)
        from tests.test_rating import _fatal

        injected = _fatal("CAP-injected-unfair", FatalClass.unfair_baseline)
        injected.metadata = {"source": "related_work_search", "candidate_ids": []}
        package.issues.append(injected)
        scored = rate_venue(package)
        self.assertEqual(scored.value, 5)
        self.assertCountEqual(scored.metadata["cap_ids"], ["CAP-missing-neighbor", "CAP-unfair-baseline"])
        self.assertNotIn("CAP-injected-unfair", scored.metadata["cap_ids"])
        self.assertEqual(scored.metadata["related_work_comparison"]["status"], "partial")
        self.assertEqual(scored.metadata["related_work_comparison"]["failed_providers"], ["openalex"])
        self.assertEqual(scored.metadata["bands_after_ceiling"]["originality"], 4.0)
        self.assertEqual(scored.metadata["bands_after_ceiling"]["experimental_soundness"], 4.0)

    def test_partial_success_without_a_prior_neighbor_emits_no_cap(self) -> None:
        def empty(url: str) -> HttpResult:
            if "export.arxiv.org" in url:
                return HttpResult(200, b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>")
            return HttpResult(429, b"slow down")

        quiet = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=empty,
            providers=["arxiv", "openalex"],
        )
        self.assertEqual(quiet.status, "partial")
        self.assertEqual(quiet.failed_providers, ["openalex"])
        self.assertEqual(quiet.candidates, [])
        self.assertEqual([signal.fatal_class for signal in quiet.cap_signals], ["unfair_baseline"])
        quiet_issues = issues_from_cap_signals(
            quiet,
            anchor_excerpt=manuscript_baseline_excerpt(MANUSCRIPT),
        )
        self.assertEqual([issue.fatal_class for issue in quiet_issues], [FatalClass.unfair_baseline])
        self.assertIsNotNone(quiet_issues[0].first_read)
        # The empty arXiv feed is a successful response and stays cached by query.
        clear_retrieval_cache()

        def concurrent(url: str) -> HttpResult:
            if "export.arxiv.org" in url:
                return HttpResult(200, _pose_atom(published="2026-10-02T00:00:00Z", title="Concurrent Pose Note"))
            raise RetrievalFailure("timeout", "timed out")

        later = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=concurrent,
            providers=["semantic_scholar", "arxiv"],
        )
        self.assertEqual(later.status, "partial")
        self.assertEqual(later.failed_providers, ["semantic_scholar"])
        self.assertTrue(later.candidates)
        self.assertTrue(all(item.date_relation == "concurrent" for item in later.candidates))
        self.assertEqual([signal.fatal_class for signal in later.cap_signals], ["unfair_baseline"])
        self.assertEqual(later.effective_venue_cap, 5)

    def test_a_failed_provider_cannot_create_or_strengthen_a_cap(self) -> None:
        candidate = RelatedCandidate(
            id="C1",
            title="A Public Baseline for Synthetic Pose Estimation",
            link="https://doi.org/10.0000/synthetic.pose.2022",
            external_id="doi:10.0000/synthetic.pose.2022",
            summary="Synthetic prior baseline.",
            cited=True,
            published="2022-06-01",
            date_relation="prior",
            required=True,
            tier="must-compare",
            reason_en="Same task.",
            reason_zh="任务相同。",
            provider="openalex",
            confirmed=True,
            confirmation_reason="Same task and setting.",
        )
        missing = BaselineGap(
            candidate_id="C1",
            title=candidate.title,
            kind="missing_baseline",
            detail_en="Missing from the baselines.",
            detail_zh="没有进入基线。",
        )
        privileged = BaselineGap(
            candidate_id=None,
            title="Scale Oracle",
            kind="privileged_baseline",
            detail_en="Privileged.",
            detail_zh="特权。",
        )
        logs = [
            RetrievalLogEntry(provider="arxiv", query_id="Q1", status="ok", detail="1 records"),
            RetrievalLogEntry(provider="openalex", query_id="Q1", status="rate_limited", detail="HTTP 429"),
        ]
        blocked = build_cap_signals(
            gaps=[missing, privileged],
            status="partial",
            candidates=[candidate],
            logs=logs,
        )
        self.assertEqual([signal.fatal_class for signal in blocked], ["unfair_baseline"])
        allowed = build_cap_signals(
            gaps=[missing, privileged],
            status="partial",
            candidates=[candidate.model_copy(update={"provider": "arxiv"})],
            logs=logs,
        )
        self.assertCountEqual(
            [signal.fatal_class for signal in allowed],
            ["missing_closest_neighbor", "unfair_baseline"],
        )
        missing_signal = next(signal for signal in allowed if signal.fatal_class == "missing_closest_neighbor")
        self.assertEqual(missing_signal.venue_cap, 6)
        self.assertEqual(missing_signal.candidate_ids, ["C1"])
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, missing_signal.evidence_ids)


def _pose_atom(*, published: str, title: str = "A Public Baseline for Synthetic Pose Estimation") -> bytes:
    return textwrap.dedent(
        f"""\
        <?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>http://arxiv.org/abs/0000.00001v1</id>
            <published>{published}</published>
            <title>{title}</title>
            <summary>Offline temporal fusion estimates a 6-DoF pose trajectory for monocular specimen pose estimation. Pose error in millimeters is measured on a sequence-level holdout.</summary>
          </entry>
        </feed>
        """
    ).encode("utf-8")


class StageTests(unittest.TestCase):
    def test_pipeline_writes_bilingual_related_work_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            for path in EXAMPLE.iterdir():
                if path.is_file():
                    shutil.copy(path, workspace / path.name)
            manifest = run_pipeline(workspace / "review.toml")
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "related_work"
            markdown = (stage_dir / "related_work.md").read_text(encoding="utf-8")
            self.assertIn("## Related Work / 相关工作", markdown)
            self.assertIn("**en:**", markdown)
            self.assertIn("**zh-Hans:**", markdown)
            self.assertIn("Suggested weakness", markdown)
            self.assertIn("建议的不足", markdown)
            self.assertNotIn("ZephyrCodename", markdown)
            self.assertNotIn("Ghost Paper", markdown)
            report = (workspace / ".runs" / manifest.run_id / "stages" / "report" / "review_report.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("## Related Work", report)
            self.assertNotIn("### Queries", report)
            self.assertNotIn("### Candidates", report)
            self.assertNotIn("## Retrieved Sources", report)
            require_bilingual_markdown(report)
            queries = json.loads((stage_dir / "queries.json").read_text(encoding="utf-8"))
            families = {query["metadata"]["family"] for query in queries}
            self.assertTrue(
                {
                    "same_problem",
                    "same_technique",
                    "alternative_formulation",
                    "standard_baselines",
                    "adjacent_tasks",
                }
                <= families
            )
            named = [query["query"] for query in queries if query["metadata"]["family"] == "named_method"]
            self.assertIn("Ribbon Fuse Estimator", named)
            self.assertTrue(all("Zephyr" not in query["query"] for query in queries))
            packet = json.loads((stage_dir / "confirmation_packet.json").read_text(encoding="utf-8"))
            self.assertIn("baselines", packet)
            self.assertIn("candidates", packet)
            judgment = json.loads((stage_dir / "judgment.json").read_text(encoding="utf-8"))
            self.assertEqual(judgment["confirmation"], "confirmed")
            run_dir = workspace / ".runs" / manifest.run_id / "stages"
            rated = json.loads((run_dir / "rate" / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(rated["overall_score"]["value"], 5)
            self.assertCountEqual(
                rated["overall_score"]["metadata"]["cap_ids"],
                ["CAP-missing-neighbor", "CAP-unfair-baseline"],
            )
            comparison = rated["overall_score"]["metadata"]["related_work_comparison"]
            self.assertEqual(comparison["status"], "complete")
            self.assertEqual(comparison["failed_providers"], [])
            self.assertNotIn("may be incomplete", report)
            self.assertNotIn("检索可能不完整", report)
            self.assertIn("assigned 8.0/10", report)
            self.assertIn("after ceiling 4.0/10", report)
            self.assertIn("**What the paper said.**", report)
            self.assertNotIn("**What the paper said / 论文写了什么.**", report)
            self.assertIn("> ", report)
            self.assertGreater(comparison["baseline_counts"]["missing"], 0)
            self.assertGreater(comparison["baseline_counts"]["privileged"], 0)
            self.assertTrue(comparison["closest_neighbour_ids"])
            novelty = json.loads((run_dir / "novelty" / "novelty.json").read_text(encoding="utf-8"))
            self.assertTrue(novelty)
            self.assertEqual(novelty[0]["metadata"]["related_work_comparison"]["status"], "complete")
            self.assertIn("closest_neighbour_ids", novelty[0]["metadata"]["related_work_comparison"])

    def test_deny_policy_blocks_live_search(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text(MANUSCRIPT, encoding="utf-8")
            (workspace / "review.toml").write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "related-deny"
                    title = "Deny live related work"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "related_work"
                    type = "related_work_search"
                    [stages.params]
                    mode = "live"
                    requires_network = true
                    network_scope = "metadata"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PipelineError, "requests metadata network access under policy deny"):
                run_pipeline(workspace / "review.toml")


    def test_execute_review_adapter_is_not_used_for_confirmation(self) -> None:
        raw = json.loads((EXAMPLE / "findings.json").read_text(encoding="utf-8"))
        raw.pop("confirmations", None)
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text(MANUSCRIPT, encoding="utf-8")
            (workspace / "findings.json").write_text(json.dumps(raw), encoding="utf-8")
            (workspace / "review.toml").write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "related-no-autobind"
                    title = "Confirmation stays on an explicit adapter"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "ingest"
                    type = "ingest"

                    [[stages]]
                    id = "related_work"
                    type = "related_work_search"
                    depends_on = ["ingest"]
                    [stages.params]
                    mode = "agent"
                    findings_path = "findings.json"

                    [[stages]]
                    id = "bands"
                    type = "execute_review"
                    enabled = false
                    depends_on = ["ingest"]
                    [stages.params]
                    adapter = "autopaperreview.model_exec:OpenAICompatibleAdapter"
                    """
                ),
                encoding="utf-8",
            )
            manifest = run_pipeline(workspace / "review.toml")
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "related_work"
            judgment = json.loads((stage_dir / "judgment.json").read_text(encoding="utf-8"))
        self.assertEqual(judgment["confirmation"], "awaiting_confirmation")
        self.assertTrue(judgment["confirmation_packet"]["candidates"])
        self.assertTrue(judgment["candidates"])
        self.assertTrue(all(not item["confirmed"] for item in judgment["candidates"]))


class TieringAndRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_retrieval_cache()

    def _payload(self, title: str, summary: str, arxiv: str, published: str = "2022-01-01T00:00:00Z") -> str:
        return (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            f"<id>http://arxiv.org/abs/{arxiv}v1</id><published>{published}</published>"
            f"<title>{title}</title><summary>{summary}</summary></entry></feed>"
        )

    def test_must_compare_requires_task_and_technique_and_drops_non_methods(self) -> None:
        overlap = (
            "Offline temporal fusion estimates a 6-DoF pose trajectory for monocular specimen pose estimation. "
            "Pose error in millimeters is measured on a sequence-level holdout."
        )
        rows = [
            ("Alpha Fusion Pose Method", overlap, "0000.00011", True),
            ("Beta Fusion Pose Method", overlap, "0000.00012", True),
            ("Gamma Fusion Pose Method", overlap, "0000.00013", True),
            ("Zeta Fusion Pose Method", overlap, "0000.00014", True),
            ("Unconfirmed Fusion Pose Method", overlap, "0000.00015", False),
            ("Agriculture Reconstruction Review", overlap, "0000.00021", True),
            ("Point Cloud Generator", overlap, "0000.00022", True),
            ("Farm Plot Dataset", overlap, "0000.00023", True),
            ("Pose Benchmark Suite", "This benchmark covers pose.", "0000.00024", True),
            ("Camera Localization Survey", "A survey of localization.", "0000.00025", True),
            ("Depth Only Note", "Accepts depth as an input modality.", "0000.00026", True),
            ("Generic Scan Registration", "A registration method for rigid scans.", "0000.00027", True),
        ]
        candidates = []
        confirmations = []
        for title, summary, arxiv, confirm in rows:
            candidates.append(
                {
                    "title": title,
                    "arxiv_id": arxiv,
                    "query_ids": ["Q-same_problem-1"],
                    "provider_payload": self._payload(title, summary, arxiv),
                }
            )
            if confirm:
                confirmations.append(
                    {
                        "external_id": f"arxiv:{arxiv}",
                        "confirmed": True,
                        "reason": "Checked against the formulated task and setting.",
                    }
                )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "findings.json"
            path.write_text(
                json.dumps({"candidates": candidates, "confirmations": confirmations}),
                encoding="utf-8",
            )
            judgment = run_related_work_search(
                MANUSCRIPT,
                mode="agent",
                findings_path=path,
                allow_network=False,
            )
        by_title = {item.title: item for item in judgment.candidates}
        required = [item for item in judgment.candidates if item.required]
        self.assertEqual(len(required), MAX_MUST_COMPARE)
        self.assertTrue(all(item.confirmed and item.confirmation_reason for item in required))
        self.assertEqual(
            {item.title for item in required},
            {"Alpha Fusion Pose Method", "Beta Fusion Pose Method", "Gamma Fusion Pose Method"},
        )
        zeta = by_title["Zeta Fusion Pose Method"]
        self.assertFalse(zeta.required)
        self.assertIn("must-compare cap", zeta.reason_en)
        unconfirmed = by_title["Unconfirmed Fusion Pose Method"]
        self.assertFalse(unconfirmed.required)
        self.assertIn("did not confirm", unconfirmed.reason_en)
        for title in (
            "Agriculture Reconstruction Review",
            "Point Cloud Generator",
            "Farm Plot Dataset",
            "Pose Benchmark Suite",
            "Camera Localization Survey",
            "Depth Only Note",
            "Generic Scan Registration",
        ):
            item = by_title[title]
            self.assertNotEqual(item.tier, "must-compare")
            self.assertFalse(item.required)
        missing = next(signal for signal in judgment.cap_signals if signal.fatal_class == "missing_closest_neighbor")
        self.assertEqual(len(missing.candidate_ids), MAX_MUST_COMPARE)
        self.assertNotIn(zeta.id, missing.candidate_ids)
        self.assertNotIn(by_title["Agriculture Reconstruction Review"].id, missing.candidate_ids)

    def test_citations_match_normalized_titles_or_external_ids(self) -> None:
        from autopaperreview.related_work import BibliographyEntry, RawHit

        entries = parse_bibliography(MANUSCRIPT)
        exact = RawHit(
            title="A Public Baseline for Synthetic Pose Estimation",
            summary="",
            venue=None,
            year=2022,
            published="2022-06-01",
            arxiv_id=None,
            doi=None,
            openalex_id=None,
            s2_id=None,
            link="https://example.invalid/exact",
            provider="arxiv",
        )
        self.assertTrue(is_cited(exact, entries))
        overlap = RawHit(
            title="Pose Estimation Survey Notes",
            summary="pose estimation",
            venue=None,
            year=2021,
            published="2021-01-01",
            arxiv_id=None,
            doi="10.0000/not.the.same",
            openalex_id=None,
            s2_id=None,
            link="https://example.invalid/overlap",
            provider="arxiv",
        )
        self.assertFalse(is_cited(overlap, entries))
        by_doi = RawHit(
            title="Different Title Entirely",
            summary="",
            venue=None,
            year=2022,
            published="2022-06-01",
            arxiv_id=None,
            doi="10.0000/synthetic.pose.2022",
            openalex_id=None,
            s2_id=None,
            link="https://example.invalid/doi",
            provider="openalex",
        )
        self.assertTrue(is_cited(by_doi, entries))
        self.assertTrue(entries)
        self.assertIsInstance(entries[0], BibliographyEntry)

    def test_section_cutter_stops_at_the_next_real_heading(self) -> None:
        leaked = MANUSCRIPT + "\n\nAblation Study\nThis later section gives ground-truth scale to an oracle.\n"
        experiments = _section(leaked, "Experiments")
        self.assertNotIn("later section", experiments)
        self.assertNotIn("Ablation Study", experiments)
        parsed = parse_experiments(leaked)
        self.assertFalse(any("later section" in item for item in parsed.privileged))
        abstract = _section(MANUSCRIPT, "Abstract")
        self.assertIn("abstract sentence", abstract.lower())
        self.assertNotIn("Task:", abstract)

    def test_named_method_query_retrieves_a_cited_closest_method(self) -> None:
        names = named_methods_from_manuscript(MANUSCRIPT)
        self.assertIn("Ribbon Fuse Estimator", names)
        self.assertNotIn("ZephyrCodename Specimen Tracker", names)
        seen: list[str] = []

        def transport(url: str) -> HttpResult:
            seen.append(url)
            if "Ribbon" in url or "ribbon" in url.lower():
                return HttpResult(
                    200,
                    _pose_atom(
                        published="2022-03-01T00:00:00Z",
                        title="Ribbon Fuse Estimator",
                    ),
                )
            return HttpResult(200, b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>")

        judgment = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=transport,
            providers=["arxiv"],
            confirm_must_compare=_confirm_must_compare,
            sleeper=lambda _seconds: None,
        )
        self.assertTrue(any("Ribbon" in url for url in seen))
        found = next(item for item in judgment.candidates if item.title == "Ribbon Fuse Estimator")
        self.assertTrue(found.required)
        self.assertTrue(found.confirmed)
        self.assertNotIn("zephyrcodename", "\n".join(seen).lower())

    def test_rate_limit_retries_and_cached_results_stay_deterministic(self) -> None:
        delays: list[float] = []
        calls: list[str] = []

        def transport(url: str) -> HttpResult:
            calls.append(url)
            if calls.count(url) == 1 and "Ribbon" in url:
                return HttpResult(429, b"slow", headers={"Retry-After": "5"})
            if "same_problem" in url or "same%20problem" in url or "Q-same" in url:
                return HttpResult(200, _pose_atom(published="2022-06-01T00:00:00Z"))
            return HttpResult(200, _pose_atom(published="2022-06-01T00:00:00Z", title="Ribbon Fuse Estimator"))

        first = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=transport,
            providers=["arxiv"],
            confirm_must_compare=_confirm_must_compare,
            sleeper=delays.append,
        )
        self.assertTrue(delays)
        self.assertEqual(delays[0], 2.0)
        ribbon_calls = [url for url in calls if "Ribbon" in url]
        self.assertGreaterEqual(len(ribbon_calls), 2)
        self.assertFalse(any(entry.status == "skipped" and entry.provider == "arxiv" for entry in first.logs))
        titles = [item.title for item in first.candidates]
        calls_after_first = len(calls)
        second = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=lambda url: (_ for _ in ()).throw(AssertionError(url)),
            providers=["arxiv"],
            confirm_must_compare=_confirm_must_compare,
            sleeper=lambda _seconds: None,
        )
        self.assertEqual(len(calls), calls_after_first)
        self.assertEqual([item.title for item in second.candidates], titles)
        self.assertTrue(any("cached" in entry.detail for entry in second.logs))

    def test_baseline_excerpt_is_one_line_and_the_bilingual_report_stays_paired(self) -> None:
        excerpt = manuscript_baseline_excerpt(MANUSCRIPT)
        self.assertNotIn("\n", excerpt)
        self.assertIn("Compared baselines", excerpt)
        self.assertIn("ground-truth scale", excerpt.lower())
        self.assertNotIn("A. Public", excerpt)
        with tempfile.TemporaryDirectory() as temporary:
            findings = Path(temporary) / "findings.json"
            findings.write_text('{"candidates": []}\n', encoding="utf-8")
            judgment = run_related_work_search(MANUSCRIPT, mode="agent", findings_path=findings)
        issues = issues_from_cap_signals(judgment, anchor_excerpt=excerpt)
        self.assertEqual([issue.fatal_class for issue in issues], [FatalClass.unfair_baseline])
        source, baseline = manuscript_baseline_records(
            MANUSCRIPT,
            sha256=None,
            locator="manuscript.txt",
        )
        package = ReviewPackage(
            project_id="synthetic-excerpt",
            manuscript=make_artifact(),
            sources=[source],
            evidence=[baseline],
            issues=issues,
        )
        self.assertEqual(
            first_read_gate_errors(
                package,
                require_first_read_on_major=True,
                require_quote_on_major=True,
            ),
            [],
        )
        markdown = render_markdown(package, languages=["en", "zh-Hans"])
        require_bilingual_markdown(markdown)
        self.assertIn("> ", markdown)
        self.assertIn("Compared baselines", markdown)

    def test_live_search_waits_for_confirmation_instead_of_demoting(self) -> None:
        seen: list[str] = []

        def transport(url: str) -> HttpResult:
            seen.append(url)
            if "export.arxiv.org" in url:
                return HttpResult(200, _pose_atom(published="2022-06-01T00:00:00Z"))
            return HttpResult(200, b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>")

        waiting = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=transport,
            providers=["arxiv"],
            sleeper=lambda _seconds: None,
        )
        self.assertTrue(seen)
        self.assertEqual(waiting.confirmation, "awaiting_confirmation")
        self.assertTrue(waiting.confirmation_packet["candidates"])
        self.assertIn("Scale Oracle", " ".join(waiting.confirmation_packet["baselines"]["privileged"]))
        provisional = next(item for item in waiting.candidates if item.title.startswith("A Public Baseline"))
        self.assertEqual(provisional.tier, "must-compare")
        self.assertTrue(provisional.required)
        self.assertFalse(provisional.confirmed)
        self.assertIn("Awaiting", provisional.reason_en)
        self.assertNotIn("did not confirm", provisional.reason_en)
        self.assertNotIn("must-compare cap", provisional.reason_en)
        self.assertEqual(
            [signal.fatal_class for signal in waiting.cap_signals],
            ["unfair_baseline"],
        )
        package = ReviewPackage(
            project_id="synthetic-awaiting",
            manuscript=make_artifact(),
            metadata={"related_work": waiting.model_dump(mode="json")},
        )
        self.assertIn(
            "awaiting_confirmation",
            " ".join(
                export_gate_errors(
                    package,
                    ExportGateParams(require_related_work_confirmation=True),
                )
            ),
        )
        report = "\n".join(render_related_work_section(waiting, ["en", "zh-Hans"]))
        self.assertIn("awaiting_confirmation", report)
        require_bilingual_markdown(report)

        clear_retrieval_cache()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "confirmations.json"
            path.write_text(
                json.dumps(
                    {
                        "confirmations": [
                            {
                                "external_id": provisional.external_id,
                                "confirmed": True,
                                "reason": "Same task and temporal fusion on a sequence-level holdout.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            calls_before = len(seen)
            confirmed = run_related_work_search(
                MANUSCRIPT,
                mode="live",
                allow_network=True,
                transport=transport,
                providers=["arxiv"],
                confirmation_path=path,
                sleeper=lambda _seconds: None,
            )
        self.assertGreater(len(seen), calls_before)
        self.assertEqual(confirmed.confirmation, "confirmed")
        kept = next(item for item in confirmed.candidates if item.external_id == provisional.external_id)
        self.assertTrue(kept.confirmed)
        self.assertTrue(kept.required)
        self.assertIn(
            "missing_closest_neighbor",
            [signal.fatal_class for signal in confirmed.cap_signals],
        )

    def test_confirmation_adapter_composes_with_live_retrieval(self) -> None:
        def transport(url: str) -> HttpResult:
            if "export.arxiv.org" in url:
                return HttpResult(200, _pose_atom(published="2022-06-01T00:00:00Z"))
            return HttpResult(200, b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>")

        FakeConfirmationAdapter.seen.clear()
        judgment = run_related_work_search(
            MANUSCRIPT,
            mode="live",
            allow_network=True,
            transport=transport,
            providers=["arxiv"],
            confirmation_adapter="tests.confirmation_fakes:FakeConfirmationAdapter",
            sleeper=lambda _seconds: None,
        )
        self.assertEqual(judgment.confirmation, "confirmed")
        self.assertTrue(any(item.confirmed for item in judgment.candidates))
        self.assertTrue(FakeConfirmationAdapter.seen)
        self.assertIn("baselines", FakeConfirmationAdapter.seen[0])

    def test_model_adapter_session_reads_the_confirmation_packet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parsed = invoke_confirmation_adapter(
                "tests.confirmation_fakes:BareConfirmationAdapter",
                {
                    "candidates": [
                        {
                            "title": "Ribbon Fuse Estimator",
                            "external_id": "arxiv:0000.00077",
                        }
                    ],
                    "baselines": {"compared": []},
                },
                scratch=Path(temporary),
                params={"base_url": "http://127.0.0.1:9/v1", "model": "synthetic-judge"},
            )
        self.assertEqual(parsed[0].title, "Ribbon Fuse Estimator")
        self.assertTrue(parsed[0].confirmed)
        self.assertIn("related_work_confirmation", RecordingModelSession.judgments)
        self.assertIn("Ribbon Fuse Estimator", RecordingModelSession.prompts[-1])

    def test_named_methods_keep_closest_and_baseline_names_ahead_of_alphabetical_titles(self) -> None:
        bibliography = "\n".join(
            f'[{index}] A. Author, "Alpha Note {index:02d}," Journal, 2020.'
            for index in range(1, 21)
        )
        text = (
            "Title: ZephyrCodename Specimen Tracker\n\n"
            "Problem\n"
            "Task: monocular specimen pose estimation\n"
            "Inputs: monocular RGB frames\n"
            "Outputs: a 6-DoF pose trajectory\n"
            "Assumptions: rigid specimen\n"
            "Setting: offline evaluation with a sequence-level holdout\n"
            "Technique: temporal fusion\n\n"
            "Related Work\n"
            "Closest method: Ribbon Fuse Estimator\n"
            "Our temporal fusion follows Near Fusion Method.\n"
            "Often Cited Estimator is popular. Often Cited Estimator is common. "
            "Often Cited Estimator is known. Often Cited Estimator remains.\n\n"
            "Experiments\n"
            "Decision metric: pose error in millimeters\n"
            "Compared baselines: Flat Image Classifier\n"
            "Privileged baselines: Scale Oracle | given ground-truth scale\n\n"
            "References\n"
            + bibliography
            + '\n[21] C. Author, "Near Fusion Method," Journal, 2021.\n'
            + '[22] D. Author, "Often Cited Estimator," Journal, 2021.\n'
            + '[23] E. Author, "Ribbon Fuse Estimator," Journal, 2021.\n'
        )
        limited = named_methods_from_manuscript(text, limit=4)
        self.assertEqual(
            limited[:4],
            [
                "Ribbon Fuse Estimator",
                "Flat Image Classifier",
                "Scale Oracle",
                "Near Fusion Method",
            ],
        )
        self.assertNotIn("Alpha Note 01", limited)
        self.assertNotIn("Often Cited Estimator", limited)
        forced = named_methods_from_manuscript(text, limit=1)
        self.assertEqual(forced, ["Ribbon Fuse Estimator"])

    def test_candidates_merge_across_provider_ids(self) -> None:
        shared = "Shared Synthetic Pose Method"
        arxiv_payload = self._payload(
            shared,
            "Offline temporal fusion estimates a 6-DoF pose trajectory for monocular specimen pose estimation.",
            "0000.00991",
            published="2021-01-01T00:00:00Z",
        )
        openalex_payload = json.dumps(
            {
                "id": "https://openalex.org/Wshared",
                "title": shared,
                "doi": "https://doi.org/10.0000/synthetic.shared.2021",
                "publication_year": 2021,
                "publication_date": "2021-01-01",
                "abstract": "Offline temporal fusion estimates a 6-DoF pose trajectory for monocular specimen pose estimation.",
            }
        )
        earlier_year = self._payload(shared, "An earlier record.", "0000.00992", published="2018-01-01T00:00:00Z")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "findings.json"
            path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "title": shared,
                                "arxiv_id": "0000.00991",
                                "query_ids": ["Q-same_problem-1"],
                                "provider_payload": arxiv_payload,
                            },
                            {
                                "title": shared,
                                "doi": "10.0000/synthetic.shared.2021",
                                "openalex_id": "https://openalex.org/Wshared",
                                "query_ids": ["Q-same_technique-1"],
                                "provider_payload": openalex_payload,
                            },
                            {
                                "title": shared,
                                "arxiv_id": "0000.00992",
                                "query_ids": ["Q-adjacent_tasks-1"],
                                "provider_payload": earlier_year,
                            },
                        ],
                        "confirmations": [],
                    }
                ),
                encoding="utf-8",
            )
            judgment = run_related_work_search(MANUSCRIPT, mode="agent", findings_path=path)
        matched = [item for item in judgment.candidates if item.title == shared]
        self.assertEqual(len(matched), 2)
        merged = next(item for item in matched if item.year == 2021)
        self.assertEqual(merged.external_id, "doi:10.0000/synthetic.shared.2021")
        self.assertCountEqual(merged.query_ids, ["Q-same_problem-1", "Q-same_technique-1"])
        self.assertEqual(merged.year, 2021)
        self.assertEqual(judgment.confirmation, "confirmed")
        earlier = next(item for item in matched if item.year == 2018)
        self.assertEqual(earlier.external_id, "arxiv:0000.00992")

    def test_candidates_merge_on_normalized_title_within_one_year_and_on_shared_ids(self) -> None:
        note = "A synthetic record with no task overlap."
        hyphen = "Foo-BAR: Temporal Fusion"
        spaced = "Foo BAR, Temporal Fusion"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "findings.json"
            path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "title": hyphen,
                                "arxiv_id": "0000.00881",
                                "query_ids": ["Q-same_problem-1"],
                                "provider_payload": self._payload(hyphen, note, "0000.00881", "2021-01-01T00:00:00Z"),
                            },
                            {
                                "title": spaced,
                                "arxiv_id": "0000.00882",
                                "query_ids": ["Q-same_technique-1"],
                                "provider_payload": self._payload(spaced, note, "0000.00882", "2022-06-01T00:00:00Z"),
                            },
                            {
                                "title": hyphen,
                                "arxiv_id": "0000.00883",
                                "query_ids": ["Q-adjacent_tasks-1"],
                                "provider_payload": self._payload(hyphen, note, "0000.00883", "2018-01-01T00:00:00Z"),
                            },
                            {
                                "title": "Alpha Shared Record",
                                "arxiv_id": "0000.00884",
                                "query_ids": ["Q-standard_baselines-1"],
                                "provider_payload": self._payload(
                                    "Alpha Shared Record", note, "0000.00884", "2010-01-01T00:00:00Z"
                                ),
                            },
                            {
                                "title": "Beta Shared Record",
                                "arxiv_id": "0000.00884",
                                "query_ids": ["Q-alternative_formulation-1"],
                                "provider_payload": self._payload(
                                    "Beta Shared Record", note, "0000.00884", "2015-01-01T00:00:00Z"
                                ),
                            },
                        ],
                        "confirmations": [],
                    }
                ),
                encoding="utf-8",
            )
            judgment = run_related_work_search(MANUSCRIPT, mode="agent", findings_path=path)
        self.assertEqual(len(judgment.candidates), 3)
        fused = next(item for item in judgment.candidates if item.year == 2021)
        self.assertCountEqual(fused.query_ids, ["Q-same_problem-1", "Q-same_technique-1"])
        distant = next(item for item in judgment.candidates if item.year == 2018)
        self.assertEqual(distant.query_ids, ["Q-adjacent_tasks-1"])
        shared = next(item for item in judgment.candidates if item.year == 2010)
        self.assertEqual(shared.external_id, "arxiv:0000.00884")
        self.assertCountEqual(shared.query_ids, ["Q-standard_baselines-1", "Q-alternative_formulation-1"])

    def test_semantic_scholar_backoff_paces_and_keeps_the_api_key_out_of_urls(self) -> None:
        clock = _Clock()
        calls: list[str] = []
        body = json.dumps(
            {
                "data": [
                    {
                        "paperId": "s2ribbon",
                        "title": "Ribbon Fuse Estimator",
                        "year": 2022,
                        "publicationDate": "2022-03-01",
                        "abstract": (
                            "Offline temporal fusion estimates a 6-DoF pose trajectory for "
                            "monocular specimen pose estimation. Pose error in millimeters is "
                            "measured on a sequence-level holdout."
                        ),
                        "externalIds": {"ArXiv": "0000.00077"},
                        "url": "https://www.semanticscholar.org/paper/s2ribbon",
                    }
                ]
            }
        ).encode("utf-8")

        def transport(url: str) -> HttpResult:
            calls.append(url)
            if calls.count(url) < 3:
                return HttpResult(429, b"slow", headers={"Retry-After": "3"})
            return HttpResult(200, body)

        os.environ["S2_API_KEY"] = "synthetic-key"
        try:
            headers = provider_request_headers(
                "https://api.semanticscholar.org/graph/v1/paper/search?query=pose"
            )
            self.assertEqual(headers.get("x-api-key"), "synthetic-key")
            judgment = run_related_work_search(
                MANUSCRIPT,
                mode="live",
                allow_network=True,
                transport=transport,
                providers=["semantic_scholar"],
                confirm_must_compare=_confirm_must_compare,
                sleeper=clock.sleep,
                clock=clock,
                rng=random.Random(0),
            )
        finally:
            os.environ.pop("S2_API_KEY", None)
            clear_retrieval_cache()
        self.assertEqual(clock.delays[0], 3.0)
        self.assertEqual(clock.delays[1], 3.0)
        self.assertIn(1.0, clock.delays)
        self.assertTrue(any(item.title == "Ribbon Fuse Estimator" for item in judgment.candidates))
        self.assertFalse(any(entry.status == "skipped" for entry in judgment.logs))
        blob = "\n".join(calls)
        self.assertNotIn("synthetic-key", blob)
        self.assertNotIn("synthetic-key", "\n".join(entry.detail for entry in judgment.logs))

    def test_compact_method_names_survive_extraction(self) -> None:
        text = textwrap.dedent(
            """\
            Title: Synthetic Compact Tracker

            Problem
            Task: monocular specimen pose estimation
            Inputs: monocular RGB frames
            Outputs: a pose trajectory
            Assumptions: rigid specimen
            Setting: offline evaluation with a sequence-level holdout
            Technique: temporal fusion

            Related Work
            Foo-BAR, Abc3D, and XYZ-Net estimate pose.
            """
        )
        names = named_methods_from_manuscript(text)
        self.assertIn("Foo-BAR", names)
        self.assertIn("Abc3D", names)
        self.assertIn("XYZ-Net", names)
        queries = generate_search_queries(derive_formulation(text), names)
        issued = [query.query for query in queries if query.metadata["family"] == "named_method"]
        self.assertIn("Foo-BAR", issued)
        self.assertIn("Abc3D", issued)
        self.assertIn("XYZ-Net", issued)

    def test_cited_bibliography_entry_30_is_resolved_by_title_and_identifier(self) -> None:
        entries = []
        for index in range(1, 41):
            if index == 30:
                entries.append(
                    '[30] Z. Author, "Foo-BAR," Synthetic Journal, 2021. '
                    "arXiv:0000.00030. doi:10.0000/synthetic.foobar.2021"
                )
            elif index == 40:
                entries.append(
                    '[40] Q. Author, "Uncited Tail Note," Synthetic Journal, 2019. arXiv:0000.00040.'
                )
            else:
                entries.append(f'[{index}] A. Author, "Filler Note {index:02d}," Synthetic Journal, 2019.')
        text = (
            "Title: Synthetic Specimen Tracker\n\n"
            "Problem\n"
            "Task: monocular specimen pose estimation\n"
            "Inputs: monocular RGB frames\n"
            "Outputs: a 6-DoF pose trajectory\n"
            "Assumptions: rigid specimen\n"
            "Setting: offline evaluation with a sequence-level holdout and pose error in millimeters\n"
            "Technique: temporal fusion\n\n"
            "Related Work\n"
            "QUILLTOKEN The closest method appears only as \\cite{30}.\n\n"
            "Experiments\n"
            "Decision metric: pose error in millimeters\n"
            "Compared baselines: Flat Image Classifier\n\n"
            "References\n" + "\n".join(entries) + "\n"
        )
        related = _section(text, "Related Work")
        self.assertIn("\\cite{30}", related)
        self.assertNotIn("Foo-BAR", related)
        overlap = (
            "Offline temporal fusion estimates a 6-DoF pose trajectory for monocular specimen pose estimation. "
            "Pose error in millimeters is measured on a sequence-level holdout."
        )
        atom = (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            "<id>http://arxiv.org/abs/0000.00030v1</id><published>2021-04-01T00:00:00Z</published>"
            f"<title>Foo-BAR</title><summary>{overlap}</summary>"
            '<link href="https://arxiv.org/abs/0000.00030"/>'
            "</entry></feed>"
        ).encode("utf-8")
        openalex = json.dumps(
            {
                "results": [
                    {
                        "id": "https://openalex.org/Wfoobar",
                        "title": "Foo-BAR",
                        "doi": "https://doi.org/10.0000/synthetic.foobar.2021",
                        "ids": {"arxiv": "0000.00030"},
                        "publication_year": 2021,
                        "publication_date": "2021-04-01",
                        "abstract": overlap,
                    }
                ]
            }
        ).encode("utf-8")
        seen: list[str] = []

        def transport(url: str) -> HttpResult:
            seen.append(url)
            if "Foo-BAR" in url or "0000.00030" in url or "synthetic.foobar.2021" in url:
                if "export.arxiv.org" in url:
                    return HttpResult(200, atom)
                return HttpResult(200, openalex)
            if "export.arxiv.org" in url:
                return HttpResult(200, b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>")
            return HttpResult(200, b'{"results":[]}')

        judgment = run_related_work_search(
            text,
            mode="live",
            allow_network=True,
            transport=transport,
            providers=["arxiv", "openalex"],
            confirm_must_compare=_confirm_must_compare,
            sleeper=lambda _seconds: None,
        )
        cited = [query for query in judgment.queries if query.metadata.get("family") == "cited_reference"]
        self.assertEqual(cited[0].metadata["marker"], "30")
        self.assertTrue(cited[0].metadata["cited_in_body"])
        lookups = {
            (query.metadata["lookup"], query.metadata["providers"][0], query.query)
            for query in cited
            if query.metadata["marker"] == "30" and query.metadata["lookup"] != "arxiv_id_list"
        }
        self.assertIn(("title", "arxiv", "Foo-BAR"), lookups)
        self.assertIn(("title", "openalex", "Foo-BAR"), lookups)
        self.assertIn(("doi", "openalex", "10.0000/synthetic.foobar.2021"), lookups)
        id_list = next(query for query in cited if query.metadata["lookup"] == "arxiv_id_list")
        self.assertIn("0000.00030", id_list.query)
        blob = "\n".join(seen)
        self.assertIn("search_query=ti:", blob)
        self.assertIn("Foo-BAR", blob)
        self.assertIn("id_list=", blob)
        self.assertIn("0000.00030", blob)
        self.assertNotIn("search_query=id:", blob)
        self.assertIn("title.search:", blob)
        self.assertIn("filter=doi:", blob)
        self.assertIn("synthetic.foobar.2021", blob)
        self.assertNotIn("QUILLTOKEN", blob)
        self.assertNotIn("api.semanticscholar.org", blob)
        narrow = run_related_work_search(text, mode="off", bibliography_lookup_limit=5)
        narrow_titles = {
            query.query
            for query in narrow.queries
            if query.metadata.get("family") == "cited_reference" and query.metadata.get("lookup") == "title"
        }
        self.assertIn("Foo-BAR", narrow_titles)
        self.assertNotIn("Uncited Tail Note", narrow_titles)
        found = next(item for item in judgment.candidates if item.title == "Foo-BAR")
        self.assertTrue(found.cited)
        self.assertEqual(found.tier, "must-compare")
        self.assertTrue(found.required)
        self.assertTrue(found.confirmed)
        self.assertIn("missing_closest_neighbor", [signal.fatal_class for signal in judgment.cap_signals])
        self.assertNotIn("Foo-BAR", " ".join(judgment.confirmation_packet["baselines"]["compared"]))

    def test_bracket_citation_24_is_resolved_from_a_wrapped_aaai_bibliography(self) -> None:
        bibliography = _wrapped_aaai_bibliography()
        text = (
            f"{_synthetic_problem()}\n\n"
            "Related Work\n"
            "QUILLTOKEN The closest method is cited only as [24].\n\n"
            "Experiments\n"
            "Decision metric: pose error in millimeters\n"
            "Compared baselines: Flat Image Classifier\n\n"
            "References\n"
            f"{bibliography}\n"
        )
        related = _section(text, "Related Work")
        self.assertIn("[24]", related)
        self.assertNotIn("Foo-BAR", related)
        self.assertNotIn("\\cite", related)
        entries = parse_bibliography(text)
        self.assertEqual(len(entries), 34)
        closest = next(entry for entry in entries if entry.marker == "24")
        self.assertEqual(closest.title, "Foo-BAR: Temporal fusion for specimen pose")
        self.assertEqual(closest.arxiv_id, "0000.00024")
        overlap = (
            "Offline temporal fusion estimates a 6-DoF pose trajectory for monocular specimen pose estimation. "
            "Pose error in millimeters is measured on a sequence-level holdout."
        )
        title = "Foo-BAR: Temporal fusion for specimen pose"
        atom = (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            "<id>http://arxiv.org/abs/0000.00024v1</id><published>2021-04-01T00:00:00Z</published>"
            f"<title>{title}</title><summary>{overlap}</summary>"
            '<link href="https://arxiv.org/abs/0000.00024"/>'
            "</entry></feed>"
        ).encode("utf-8")
        seen: list[str] = []

        def transport(url: str) -> HttpResult:
            seen.append(url)
            if "Foo-BAR" in url or "0000.00024" in url:
                if "export.arxiv.org" in url:
                    return HttpResult(200, atom)
                return HttpResult(200, b'{"results":[]}')
            if "export.arxiv.org" in url:
                return HttpResult(200, b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>")
            return HttpResult(200, b'{"results":[]}')

        judgment = run_related_work_search(
            text,
            mode="live",
            allow_network=True,
            transport=transport,
            providers=["arxiv", "openalex"],
            confirm_must_compare=_confirm_must_compare,
            sleeper=lambda _seconds: None,
        )
        cited = [query for query in judgment.queries if query.metadata.get("family") == "cited_reference"]
        self.assertEqual(cited[0].metadata["marker"], "24")
        self.assertTrue(cited[0].metadata["cited_in_body"])
        id_list = next(query for query in cited if query.metadata["lookup"] == "arxiv_id_list")
        self.assertIn("0000.00024", id_list.query)
        self.assertIn("0000.00001", id_list.query)
        blob = "\n".join(seen)
        id_urls = [url for url in seen if "id_list=" in url]
        self.assertEqual(len(id_urls), 1)
        self.assertIn("0000.00024", id_urls[0])
        self.assertIn("max_results=2", id_urls[0])
        self.assertNotIn("search_query=id:", blob)
        self.assertNotIn("QUILLTOKEN", blob)
        self.assertNotIn("api.semanticscholar.org", blob)
        found = next(item for item in judgment.candidates if item.title == title)
        self.assertTrue(found.cited)
        self.assertEqual(found.tier, "must-compare")
        self.assertTrue(found.required)
        packet_titles = [row["title"] for row in judgment.confirmation_packet["candidates"]]
        self.assertIn(title, packet_titles)
        baselines = judgment.confirmation_packet["baselines"]
        baseline_text = " ".join([*baselines["compared"], *baselines["privileged"], *baselines["different_task"]])
        self.assertNotIn("Foo-BAR", baseline_text)
        narrow = run_related_work_search(text, mode="off", bibliography_lookup_limit=5)
        narrow_markers = {
            query.metadata["marker"]
            for query in narrow.queries
            if query.metadata.get("family") == "cited_reference" and query.metadata.get("lookup") == "title"
        }
        self.assertIn("24", narrow_markers)
        self.assertLessEqual(len(narrow_markers), 5)
        self.assertNotIn("6", narrow_markers)

    def test_openalex_title_filters_are_sanitized_and_http_400_is_cached(self) -> None:
        raw_title = "Foo-BAR: Pose, Held-Out (Synthetic)"
        text = f"""{_synthetic_problem()}

Related Work
A cited note [1].

References
[1] A. Author, "{raw_title}," Synthetic Journal, 2021.
"""
        clock = _Clock()
        clock.now = 1_000_000.0
        calls: list[str] = []

        def transport(url: str) -> HttpResult:
            calls.append(url)
            if "title.search:" in url:
                return HttpResult(400, b'{"error":"Invalid query"}')
            return HttpResult(200, b'{"results":[]}')

        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary) / "related_work_cache"
            kwargs = {
                "mode": "live",
                "allow_network": True,
                "providers": ["openalex"],
                "sleeper": lambda _seconds: None,
                "cache_dir": cache_dir,
                "cache_clock": clock,
            }
            run_related_work_search(text, transport=transport, **kwargs)
            title_urls = [url for url in calls if "title.search:" in url]
            self.assertTrue(title_urls)
            for url in title_urls:
                value = url.split("title.search:", 1)[1].split("&", 1)[0]
                self.assertNotIn("%3A", value)
                self.assertNotIn("%2C", value)
                self.assertNotIn("%28", value)
                self.assertNotIn(",", value)
                self.assertIn("Foo-BAR%20Pose%20Held-Out%20Synthetic", value)
            stored = [json.loads(path.read_text(encoding="utf-8")) for path in cache_dir.glob("*.json")]
            self.assertTrue(any(item["status"] == 400 for item in stored))
            clear_retrieval_cache()
            after_first = len(calls)
            run_related_work_search(
                text,
                transport=lambda url: (_ for _ in ()).throw(AssertionError(url)),
                **kwargs,
            )
            self.assertEqual(len(calls), after_first)

    def test_judge_reasons_stay_verbatim_in_the_judgment_and_the_report(self) -> None:
        confirm = "Confirmed because the synthetic holdout uses the same temporal fusion."
        reject = "Rejected because this synthetic record uses a privileged depth channel."

        def judge(candidate: RelatedCandidate) -> tuple[bool, str]:
            if candidate.title == "A Public Baseline for Synthetic Pose Estimation":
                return True, confirm
            if candidate.title == "Depth Aware Pose Estimator":
                return False, reject
            return False, ""

        raw = json.loads((EXAMPLE / "findings.json").read_text(encoding="utf-8"))
        raw.pop("confirmations", None)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "findings.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            judged = run_related_work_search(
                MANUSCRIPT,
                mode="agent",
                findings_path=path,
                confirm_must_compare=judge,
            )
        by_title = {item.title: item for item in judged.candidates}
        kept = by_title["A Public Baseline for Synthetic Pose Estimation"]
        dropped = by_title["Depth Aware Pose Estimator"]
        self.assertEqual(kept.confirmation_reason, confirm)
        self.assertIn(confirm, kept.reason_en)
        self.assertEqual(dropped.confirmation_reason, reject)
        self.assertIn(reject, dropped.reason_en)
        self.assertNotIn("did not confirm", dropped.reason_en)
        report = "\n".join(render_related_work_section(judged, ["en", "zh-Hans"]))
        self.assertIn(confirm, report)
        self.assertIn(reject, report)

        raw["confirmations"] = [
            {
                "external_id": "doi:10.0000/synthetic.pose.2022",
                "confirmed": True,
                "reason": confirm,
            },
            {
                "external_id": "arxiv:0000.00002",
                "confirmed": False,
                "reason": reject,
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "findings.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            filed = run_related_work_search(MANUSCRIPT, mode="agent", findings_path=path)
        filed_by_title = {item.title: item for item in filed.candidates}
        self.assertEqual(filed_by_title["A Public Baseline for Synthetic Pose Estimation"].confirmation_reason, confirm)
        self.assertEqual(filed_by_title["Depth Aware Pose Estimator"].confirmation_reason, reject)
        filed_report = "\n".join(render_related_work_section(filed, ["en"]))
        self.assertIn(confirm, filed_report)
        self.assertIn(reject, filed_report)
        self.assertNotIn("did not confirm", filed_by_title["Depth Aware Pose Estimator"].reason_en)

    def test_disk_cache_skips_the_transport_until_the_ttl_expires(self) -> None:
        clock = _Clock()
        clock.now = 1_000_000.0
        calls: list[str] = []

        def transport(url: str) -> HttpResult:
            calls.append(url)
            return HttpResult(200, b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>")

        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary) / "related_work_cache"
            kwargs = {
                "mode": "live",
                "allow_network": True,
                "providers": ["arxiv"],
                "sleeper": lambda _seconds: None,
                "cache_dir": cache_dir,
                "cache_clock": clock,
            }
            run_related_work_search(MANUSCRIPT, transport=transport, **kwargs)
            self.assertTrue(calls)
            stored = list(cache_dir.glob("*.json"))
            self.assertTrue(stored)
            self.assertTrue(all(len(path.stem) == 64 for path in stored))
            self.assertNotIn("S2_API_KEY", stored[0].read_text(encoding="utf-8"))
            clear_retrieval_cache()
            after_first = len(calls)
            run_related_work_search(
                MANUSCRIPT,
                transport=lambda url: (_ for _ in ()).throw(AssertionError(url)),
                **kwargs,
            )
            self.assertEqual(len(calls), after_first)
            clock.now += RETRIEVAL_CACHE_TTL_SECONDS + 1
            clear_retrieval_cache()
            run_related_work_search(MANUSCRIPT, transport=transport, **kwargs)
            self.assertGreater(len(calls), after_first)

    def test_unauthenticated_semantic_scholar_is_skipped_inside_the_time_budget(self) -> None:
        clock = _Clock()
        previous = os.environ.pop("S2_API_KEY", None)

        def transport(url: str) -> HttpResult:
            if "semanticscholar" in url:
                return HttpResult(429, b"slow", headers={"Retry-After": "60"})
            return HttpResult(200, _pose_atom(published="2022-06-01T00:00:00Z"))

        try:
            judgment = run_related_work_search(
                MANUSCRIPT,
                mode="live",
                allow_network=True,
                transport=transport,
                providers=["arxiv", "semantic_scholar"],
                confirm_must_compare=_confirm_must_compare,
                sleeper=clock.sleep,
                clock=clock,
                rng=random.Random(0),
            )
        finally:
            if previous is not None:
                os.environ["S2_API_KEY"] = previous
            clear_retrieval_cache()
        self.assertGreater(sum(clock.delays), 0)
        self.assertLessEqual(sum(clock.delays), S2_UNAUTH_BUDGET_SECONDS)
        self.assertLess(max(clock.delays), 60)
        self.assertEqual(judgment.status, "partial")
        self.assertIn("semantic_scholar", judgment.failed_providers)
        self.assertTrue(
            any(
                entry.provider == "semantic_scholar" and entry.status == "skipped" and "S2_API_KEY" in entry.detail
                for entry in judgment.logs
            )
        )
        self.assertTrue(any(item.title.startswith("A Public Baseline") for item in judgment.candidates))


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.now += seconds


if __name__ == "__main__":
    unittest.main()
