import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autopaperreview.proposal import contrast, exemplars, preference
from autopaperreview.proposal.prior_index import build, norm_arxiv, retrieve


PROPOSAL = {"problem": "Use sparse archival indexing to scan diaries."}


def example(key, grade, **changes):
    return {
        "key": key,
        "split": "dev",
        "year": 2020,
        "revision_ms": 10,
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
            {"arxiv_id": "https://export.arxiv.org/pdf/9999.98766v2.pdf?source=test#intro"},
        ):
            with self.subTest(changes=changes):
                self.assertNotIn(candidate["key"], opinions({**candidate, **changes}, **options))
        self.assertNotIn(candidate["key"], opinions({**candidate, "year": 2050}))
        self.assertNotIn(candidate["key"], opinions({**candidate, "revision_ms": 2_500_000_000_000}))

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
        packet, _ = contrast.packet(PROPOSAL, [*peers, *bad_hits], cutoff_ms=20)
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
        with TemporaryDirectory(prefix="apr-pr70-fix-r1-") as folder:
            source, db = Path(folder) / "source.jsonl", Path(folder) / "prior.sqlite"
            source.write_text("".join(
                json.dumps({**row, "forum": key, **changes}) + "\n" for key, changes in variants.items()
            ), encoding="utf-8")
            build(source, db)
            with sqlite3.connect(db) as conn:
                found = {name for (name,) in conn.execute("SELECT forum FROM papers")}
        self.assertEqual(found, {"synthetic-only-partition", "synthetic-agree"})

    def test_prior_filters_legacy_rows_before_ranking_and_limiting(self):
        with TemporaryDirectory(prefix="apr-pr70-fix-r1-") as folder:
            db = Path(folder) / "prior.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE VIRTUAL TABLE papers USING fts5(title, abstract, year UNINDEXED, "
                    "revision UNINDEXED, forum UNINDEXED, arxiv UNINDEXED, split UNINDEXED, "
                    "partition UNINDEXED, cohort UNINDEXED)"
                )
                conn.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(papers,row)")
                rows = [
                    ("Sparse archival indexing", "Sparse archival indexing", 2020, "10trailing",
                     f"synthetic-bad-{i}", "", "dev", None, None)
                    for i in range(70)
                ]
                rows.extend([
                    ("Sparse archival indexing", "A fixture.", 2020, 10, "synthetic-arxiv",
                     "https://export.arxiv.org/abs/9999.98766v3?src=test#part", "dev", None, None),
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
                ])
                rows.extend([
                    ("Metal smelting", "Smelting in copper pots", 2019, 8, f"synthetic-background-{i}",
                     "", "dev", None, None)
                    for i in range(300)
                ])
                conn.executemany("INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?)", rows)
            options = {"limit": 1, "exclude_arxiv": "9999.98766", "cutoff_ms": 20}
            for year in (2024, None):
                with self.subTest(year=year):
                    hits = retrieve(db, PROPOSAL, target_year=year, **options)
                    self.assertEqual([hit["forum"] for hit in hits], ["synthetic-eligible"])

    def test_arxiv_normalization_covers_hosts_formats_and_legacy_numbers(self):
        self.assertEqual(norm_arxiv("arXiv:math/0301001v2#abstract"), "math/0301001")
        self.assertEqual(norm_arxiv("https://export.arxiv.org/pdf/math/0301001v7.pdf?x=1#top"), "math/0301001")
        self.assertEqual(norm_arxiv("//www.arxiv.org/html/9999.98766v2?x=1#top"), "9999.98766")


if __name__ == "__main__":
    unittest.main()
