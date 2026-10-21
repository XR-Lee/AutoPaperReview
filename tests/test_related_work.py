"""Synthetic related-work search. No real manuscripts or real paper titles."""

from __future__ import annotations

import json
import shutil
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path

from autopaperreview.bilingual_coverage import require_bilingual_markdown
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
    build_cap_signals,
    classify_date,
    derive_formulation,
    evidence_id_for_candidate,
    generate_search_queries,
    issues_from_cap_signals,
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


class AgentAndLiveTests(unittest.TestCase):
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
        self.assertIn("depth", depth.reason_en.lower())
        self.assertEqual(depth.external_id, "arxiv:0000.00002")
        self.assertEqual(adjacent.tier, "optional")
        self.assertFalse(adjacent.required)
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
                <summary>A standard baseline and benchmark for single-frame pose estimation. It estimates a 6-DoF pose trajectory from monocular RGB frames.</summary>
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
        self.assertEqual(verified.date_relation, "prior")
        classes = {signal.fatal_class: signal for signal in judgment.cap_signals}
        self.assertEqual(set(classes), {"missing_closest_neighbor"})
        missing = classes["missing_closest_neighbor"]
        self.assertEqual(missing.venue_cap, 6)
        self.assertEqual(missing.candidate_ids, [verified.id])
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, missing.evidence_ids)
        self.assertIn(evidence_id_for_candidate(verified.id), missing.evidence_ids)
        self.assertEqual(judgment.effective_venue_cap, 6)
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
        self.assertEqual([issue.fatal_class for issue in issues], [FatalClass.missing_closest_neighbor])
        self.assertEqual(issues[0].metadata["candidate_providers"], ["arxiv"])

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
        )
        self.assertEqual(judgment.status, "partial")
        self.assertEqual(judgment.failed_providers, ["openalex"])
        issues = issues_from_cap_signals(
            judgment,
            anchor_excerpt=manuscript_baseline_excerpt(MANUSCRIPT),
        )
        self.assertEqual([issue.fatal_class for issue in issues], [FatalClass.missing_closest_neighbor])
        package = self._scored_package(judgment, issues)
        from tests.test_rating import _fatal

        injected = _fatal("CAP-injected-unfair", FatalClass.unfair_baseline)
        injected.metadata = {"source": "related_work_search", "candidate_ids": []}
        package.issues.append(injected)
        scored = rate_venue(package)
        self.assertEqual(scored.value, 6)
        self.assertEqual(scored.metadata["cap_ids"], ["CAP-missing-neighbor"])
        self.assertEqual(scored.metadata["related_work_comparison"]["status"], "partial")
        self.assertEqual(scored.metadata["related_work_comparison"]["failed_providers"], ["openalex"])
        self.assertEqual(scored.metadata["bands_after_ceiling"]["originality"], 4.0)
        self.assertEqual(scored.metadata["bands_after_ceiling"]["experimental_soundness"], 8.0)

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
        self.assertEqual(quiet.cap_signals, [])
        self.assertEqual(
            issues_from_cap_signals(quiet, anchor_excerpt=manuscript_baseline_excerpt(MANUSCRIPT)),
            [],
        )

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
        self.assertEqual(later.cap_signals, [])
        self.assertIsNone(later.effective_venue_cap)

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
        self.assertEqual(blocked, [])
        allowed = build_cap_signals(
            gaps=[missing, privileged],
            status="partial",
            candidates=[candidate.model_copy(update={"provider": "arxiv"})],
            logs=logs,
        )
        self.assertEqual([signal.fatal_class for signal in allowed], ["missing_closest_neighbor"])
        self.assertEqual(allowed[0].venue_cap, 6)
        self.assertEqual(allowed[0].candidate_ids, ["C1"])
        self.assertIn(MANUSCRIPT_BASELINE_EVIDENCE_ID, allowed[0].evidence_ids)


def _pose_atom(*, published: str, title: str = "A Public Baseline for Synthetic Pose Estimation") -> bytes:
    return textwrap.dedent(
        f"""\
        <?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>http://arxiv.org/abs/0000.00001v1</id>
            <published>{published}</published>
            <title>{title}</title>
            <summary>A standard baseline and benchmark for single-frame pose estimation. It estimates a 6-DoF pose trajectory from monocular RGB frames.</summary>
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
            self.assertIn("## Related Work / 相关工作", report)
            require_bilingual_markdown(report)
            queries = json.loads((stage_dir / "queries.json").read_text(encoding="utf-8"))
            self.assertEqual(len(queries), 5)
            self.assertTrue(all("Zephyr" not in query["query"] for query in queries))
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


if __name__ == "__main__":
    unittest.main()
