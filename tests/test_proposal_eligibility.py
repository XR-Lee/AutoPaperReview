import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.proposal import contrast, exemplars, preference, priority, stages
from autopaperreview.proposal.data import digest, write
from autopaperreview.proposal.prior_index import build, norm_arxiv, retrieve


PROPOSAL = {"problem": "Use sparse archival indexing to scan diaries."}


def example(key, grade, **changes):
    return {
        "key": key,
        "split": "dev",
        "year": 2020,
        "revision_ms": 10,
        "opinion_ms": 11,
        "target": grade,
        "proposal": {"problem": "Catalogue ledger pages by the shape of paper tabs."},
        "opinions": [key],
        **changes,
    }


class ProposalEligibilityTests(unittest.TestCase):
    def test_calibration_example_banks_share_the_development_whitelist(self):
        bank = [{
            **example(f"synthetic-{i}", 1 + i / 4),
            "partition": "development",
            "excerpts": ["A synthetic opinion."],
        } for i in range(16)]
        ineligible = [
            {**bank[0], "key": "synthetic-test", "partition": "test"},
            {**bank[1], "key": "synthetic-holdout", "cohort": "holdout"},
        ]
        self.assertEqual(exemplars.packet(PROPOSAL, bank + ineligible), exemplars.packet(PROPOSAL, bank))
        self.assertEqual(preference.panel(PROPOSAL, bank + ineligible, 0), preference.panel(PROPOSAL, bank, 0))

    def test_contrast_requires_earlier_development_records(self):
        peers = [example(f"synthetic-peer-{i}", grade) for i, grade in enumerate((1, 2, 4, 5))]
        candidate = example(
            "synthetic-candidate", 3,
            proposal={"problem": "Use sparse archival indexing to scan workshop labels."},
        )

        def opinions(row, **options):
            packet, _ = contrast.packet(PROPOSAL, [*peers, row], **options)
            return {text for item in packet["examples"].values() for text in item["reviewer_excerpts"]}

        options = {"target_year": 2024, "cutoff_ms": 20, "exclude_arxiv": "arxiv:9999.98766"}
        self.assertIn(candidate["key"], opinions(candidate, **options))
        self.assertIn(candidate["key"], opinions(
            {**candidate, "split": "", "partition": " Dev ", "cohort": "Development", "revision_ms": "+19"},
            **options,
        ))
        for changes in (
            {"split": "dev", "partition": "TEST"},
            {"split": "dev", "cohort": "hold out"},
            {"split": "unknown"},
            {"split": None},
            {"year": 2024},
            {"year": None},
            {"year": "2020trailing"},
            {"revision_ms": 20},
            {"revision_ms": 21},
            {"revision_ms": None},
            {"revision_ms": 0},
            {"revision_ms": "tomorrow"},
            {"revision_ms": "10trailing"},
            {"arxiv_id": "HTTPS://EXPORT.ARXIV.ORG./PDF/9999%2E98766V2.PDF?source=test#intro"},
            {"arxiv_id": "https://export%2Earxiv.org/abs/9999.98766"},
            {"revision": 21},
        ):
            with self.subTest(changes=changes):
                self.assertNotIn(candidate["key"], opinions({**candidate, **changes}, **options))
        self.assertNotIn(candidate["key"], opinions({**candidate, "year": 2050}, **options))
        self.assertNotIn(candidate["key"], opinions({**candidate, "revision_ms": 2_500_000_000_000}, **options))

    def test_contrast_removes_ineligible_rows_before_shortlist(self):
        peers = [example(f"synthetic-peer-{i}", grade) for i, grade in enumerate((1, 2, 4, 5))]
        bad_hits = [
            example(
                f"synthetic-hit-{i}", 5,
                revision_ms="10trailing",
                proposal={"problem": "Use sparse archival indexing to scan workshop labels."},
            )
            for i in range(70)
        ]
        packet, _ = contrast.packet(PROPOSAL, [*peers, *bad_hits], target_year=2024, cutoff_ms=20)
        self.assertEqual(
            {text for item in packet["examples"].values() for text in item["reviewer_excerpts"]},
            {row["key"] for row in peers},
        )

    def test_prior_build_applies_development_aliases_and_strict_dates(self):
        row = {"title": "A ledger catalogue", "abstract": "A synthetic summary", "year": 2020, "revision_ms": 10}
        variants = {
            "synthetic-only-partition": {"partition": "development"},
            "synthetic-agree": {"split": " DEV ", "cohort": "development", "revision_ms": "+10"},
            "synthetic-partition-conflict": {"split": "dev", "partition": "test"},
            "synthetic-cohort-conflict": {"split": "dev", "cohort": "holdout"},
            "synthetic-ambiguous": {"split": "other"},
            "synthetic-missing": {},
            "synthetic-bad-date": {"split": "dev", "revision_ms": "10trailing"},
            "synthetic-huge-date": {"split": "dev", "revision_ms": 10 ** 50},
        }
        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as folder:
            source, db = Path(folder) / "source.jsonl", Path(folder) / "prior.sqlite"
            source.write_text("".join(
                json.dumps({**row, "forum": key, **changes}) + "\n" for key, changes in variants.items()
            ), encoding="utf-8")
            build(source, db)
            with sqlite3.connect(db) as conn:
                found = {name for (name,) in conn.execute("SELECT forum FROM papers")}
        self.assertEqual(found, {"synthetic-only-partition", "synthetic-agree"})

    def test_prior_filters_legacy_rows_before_ranking_and_limiting(self):
        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as folder:
            db = Path(folder) / "prior.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE VIRTUAL TABLE papers USING fts5(title, abstract, year UNINDEXED, "
                    "revision UNINDEXED, forum UNINDEXED, arxiv UNINDEXED, split UNINDEXED, "
                    "partition UNINDEXED, cohort UNINDEXED, revision_ms UNINDEXED)"
                )
                conn.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(papers,row)")
                rows = [
                    ("Sparse archival indexing", "Sparse archival indexing", 2020, "10trailing",
                     f"synthetic-bad-{i}", "", "dev", None, None)
                    for i in range(70)
                ]
                rows.extend([
                    ("Sparse archival indexing", "A fixture.", 2020, 10, "synthetic-arxiv",
                     "HTTPS://EXPORT.ARXIV.ORG./ABS/9999%2E98766V3?src=test#part", "dev", None, None),
                    ("Sparse archival indexing", "A fixture.", 2020, 10, "synthetic-conflict",
                     "", "dev", "holdout", "dev"),
                    ("Sparse archival indexing", "A fixture.", 2020, 10, "synthetic-unknown",
                     "", "evaluation", None, None),
                    ("Sparse archival indexing", "A fixture.", 2020, 20, "synthetic-equal",
                     "", "dev", None, None),
                    ("Sparse archival indexing", "A fixture.", 2020, None, "synthetic-missing",
                     "", "dev", None, None),
                    ("Sparse archival indexing", "A fixture.", "2020suffix", 10, "synthetic-year",
                     "", "dev", None, None),
                    ("Sparse archival indexing", "A fixture.", 2050, 10, "synthetic-future",
                     "", "dev", None, None),
                    ("Catalogue methods", "We discuss archival indexing for bound records.", 2020, "+19",
                     "synthetic-eligible", "", None, "development", "dev"),
                    ("Archive shelf", "Archival indexing of envelopes.", 2020, 10,
                     "synthetic-companion", "", "dev", None, None),
                ])
                rows.extend([
                    ("Metal smelting", "Smelting in copper pots", 2019, 8, f"synthetic-background-{i}",
                     "", "dev", None, None)
                    for i in range(300)
                ])
                conn.executemany("INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?,NULL)", rows)
                conn.execute(
                    "INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("Sparse archival indexing", "A fixture.", 2020, 10, "synthetic-late-alias", "", "dev", None, None, 30),
                )
            hits = retrieve(db, PROPOSAL, limit=2, exclude_arxiv="9999.98766", target_year=2024, cutoff_ms=20)
            self.assertEqual({hit["forum"] for hit in hits}, {"synthetic-eligible", "synthetic-companion"})

    def test_prior_uses_only_eligible_rows_for_frequency_and_ranking(self):
        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as folder:
            database = Path(folder) / "prior.sqlite"
            with sqlite3.connect(database) as conn:
                conn.execute(
                    "CREATE VIRTUAL TABLE papers USING fts5(title, abstract, year UNINDEXED, "
                    "revision UNINDEXED, forum UNINDEXED, split UNINDEXED)"
                )
                conn.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(papers,row)")
                conn.executemany("INSERT INTO papers VALUES (?,?,?,?,?,?)", [
                    ("Archival labels", "Index household notes.", 2020, 10, "synthetic-first", "dev"),
                    ("Indexing labels", "Index household notes.", 2021, 11, "synthetic-second", "dev"),
                    ("Libraries", "Archival indexing of fabrics. " + "Workshop notes. " * 25,
                     2021, 12, "synthetic-support", "dev"),
                    *[("Metal shapes", "Model ingots.", 2022, 8, f"synthetic-other-{i}", "dev") for i in range(20)],
                ])
            options = {"target_year": 2024, "cutoff_ms": 20, "limit": 2}
            initial = retrieve(database, PROPOSAL, **options)
            self.assertEqual([hit["forum"] for hit in initial], ["synthetic-first", "synthetic-second"])
            with sqlite3.connect(database) as conn:
                conn.executemany("INSERT INTO papers VALUES (?,?,?,?,?,?)", [
                    ("Sparse archival labels", "Sparse archival labels.", 2020, "10suffix", f"synthetic-ineligible-{i}", "dev")
                    for i in range(70)
                ])
            self.assertEqual(retrieve(database, PROPOSAL, **options), initial)

    def test_missing_historical_bounds_fail_before_retrieval_or_model(self):
        target = {"problem": "Investigate archival indexing of workshop log labels."}
        profile = {"ranker": {"names": ["field_priority"]}}
        for year, cutoff, message in ((None, 20, "target year"), (2024, None, "cutoff")):
            with self.subTest(year=year, cutoff=cutoff):
                with patch("autopaperreview.proposal.prior_index._connect") as connect:
                    with self.assertRaisesRegex(ValueError, message):
                        retrieve(Path("synthetic-absent.sqlite"), target, target_year=year, cutoff_ms=cutoff)
                    connect.assert_not_called()
                with self.assertRaisesRegex(ValueError, message):
                    contrast.packet(target, [], target_year=year, cutoff_ms=cutoff)
                with patch("autopaperreview.proposal.priority.apply_leak_audit") as audit:
                    with self.assertRaisesRegex(ValueError, message):
                        priority.rank(target, Path("."), profile, Path("."), year=year, cutoff_ms=cutoff,
                                      leak_judge=lambda _: {"verdict": "clean"})
                    audit.assert_not_called()

    def test_field_transport_filters_undated_current_and_future_opinions(self):
        profile = {"ranker": {"method": "mean", "names": ["field_priority"]},
                   "prompts": {"field": digest(priority.PROMPTS["field"])}}
        hits = [{"forum": "synthetic-archive", "title": "Indexing labels", "abstract": "A sample abstract.",
                 "year": 2020, "revision_ms": 8}]
        opinions = {"synthetic-archive": [
            {"opinion": "The log could help visitors.", "opinion_ms": 19},
            {"opinion": "A later opinion.", "review_ms": 21},
            {"opinion": "An opinion at the cutoff.", "timestamp_ms": 20},
            {"opinion": "An undated opinion."},
            {"opinion": "A changed opinion.", "cdate": 11, "mdate": 22},
            {"opinion": "An invalid opinion time.", "opinion_ms": False},
        ]}
        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as folder:
            root = Path(folder)
            write(root / "prior-opinions.json", opinions)

            def transport(_argv, *, system, prompt, **_options):
                self.assertEqual(system, priority.PROMPTS["field"])
                body = json.loads(prompt)
                self.assertEqual(body["earlier_research"][0]["earlier_peer_opinions"], ["The log could help visitors."])
                return json.dumps({
                    "alternative": "A paper catalogue", "reason": "A smaller index", "objection": "Scope",
                    **dict.fromkeys(("advance", "interest", "constraint", "priority"), 3),
                }), 0

            with (patch.object(priority, "retrieve", return_value=hits),
                  patch.object(stages, "run_command", side_effect=transport) as run):
                result = priority.rank(PROPOSAL, root, profile, root, year=2024, cutoff_ms=20,
                                       leak_judge=lambda _: {"verdict": "clean"})
            run.assert_called_once()
            self.assertEqual(result["overall"], 3)

    def test_contrast_uses_the_opinion_timestamp_and_distinct_keys(self):
        peers = [example(f"synthetic-peer-{i}", grade) for i, grade in enumerate((1, 2, 4, 5))]
        peers[0]["opinions"] = [
            {"text": "An earlier opinion.", "opinion_ms": 12},
            {"text": "A later opinion.", "opinion_ms": 23},
            {"text": "An undated opinion."},
        ]
        aliases = [dict(peers[0], target=5, opinions=["A duplicate example."])]
        packet, scores = contrast.packet(PROPOSAL, [*peers, *aliases], target_year=2024, cutoff_ms=20)
        self.assertEqual(len(scores), 4)
        excerpts = {text for item in packet["examples"].values() for text in item["reviewer_excerpts"]}
        self.assertIn("An earlier opinion.", excerpts)
        self.assertNotIn("A later opinion.", excerpts)
        self.assertNotIn("An undated opinion.", excerpts)
        self.assertNotIn("A duplicate example.", excerpts)

    def test_contrast_excludes_grades_without_pre_cutoff_provenance(self):
        peers = [example(f"synthetic-peer-{i}", grade) for i, grade in enumerate((1, 2, 4, 5))]
        candidate = example("synthetic-close", 3, opinion_ms=None,
                            proposal={"problem": "Use sparse archival indexing to scan workshop labels."},
                            opinions=[{"text": "The labels could be useful.", "review_ms": 19}])
        options = {"target_year": 2024, "cutoff_ms": 20}
        self.assertIn(3, contrast.packet(PROPOSAL, [*peers, candidate], **options)[1].values())
        for row in (dict(candidate, target_ms=21), dict(candidate, opinions=["Unknown date."])):
            with self.subTest(row=row):
                self.assertNotIn(3, contrast.packet(PROPOSAL, [*peers, row], **options)[1].values())

    def test_arxiv_normalization_covers_hosts_formats_and_legacy_numbers(self):
        self.assertEqual(norm_arxiv("arXiv:math/0301001v2#abstract"), "math/0301001")
        self.assertEqual(norm_arxiv("https://export.arxiv.org/pdf/math/0301001v7.pdf?x=1#top"), "math/0301001")
        self.assertEqual(norm_arxiv("//www.arxiv.org/html/9999.98766v2?x=1#top"), "9999.98766")
        self.assertEqual(norm_arxiv("HTTPS://ARXIV.ORG.:443/PDF/9999%2e98766V2.PDF"), "9999.98766")
        self.assertEqual(norm_arxiv("HTTPS://ARXIV%2eORG/ABS/MATH%2F0301001V5"), "math/0301001")


if __name__ == "__main__":
    unittest.main()
