"""Proposal-only packets and a supplied prior-work pool. Synthetic records only."""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path

from autopaperreview.pipeline import run_pipeline
from autopaperreview.related_work import run_related_work_search, work_id_keys


def _proposal() -> str:
    return textwrap.dedent(
        """\
        Title: Synthetic Widget Counter

        Abstract
        We propose to count synthetic widgets with a toy recurrence.
        """
    ).strip()


class ProposalInputTests(unittest.TestCase):
    def test_pool_keeps_prior_work_and_logs_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = root / "pool.json"
            pool.write_text(
                json.dumps(
                    {
                        "pool": [
                            {
                                "id": "synthetic-prior",
                                "title": "Synthetic Prior Note",
                                "abstract": "A prior method for counting synthetic widgets in a toy lab.",
                                "date": "2024-05-01",
                            },
                            {
                                "id": "synthetic-target",
                                "title": "Synthetic Target Note",
                                "abstract": "The target record that must not be retrieved.",
                                "date": "2024-06-01",
                            },
                            {
                                "id": "synthetic-later",
                                "title": "Synthetic Later Note",
                                "abstract": "A record dated on the cutoff.",
                                "date": "2026-03-01",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def _transport(*_args, **_kwargs):
                raise AssertionError("pool mode must not call a provider")

            judgment = run_related_work_search(
                _proposal(),
                mode="pool",
                pool_path=pool,
                cutoff_date=date(2026, 3, 1),
                blocked_ids=["synthetic-target"],
                transport=_transport,
            )

        titles = [item.title for item in judgment.candidates]
        self.assertEqual(titles, ["Synthetic Prior Note"])
        self.assertEqual(judgment.status, "complete")
        self.assertEqual(judgment.cutoff_date, "2026-03-01")
        self.assertTrue(any(item.endswith("synthetic-target") for item in judgment.target_leakage))
        self.assertTrue(
            any("dated on or after cutoff" in entry.detail for entry in judgment.logs)
        )
        self.assertIn("counting synthetic widgets", judgment.candidates[0].summary)
        self.assertTrue(any(query.id == "Q-pool" for query in judgment.queries))

    def test_pool_does_not_refuse_a_title_inside_the_description(self) -> None:
        text = textwrap.dedent(
            """\
            Title: Synthetic Widget Counter

            Task: Synthetic Widget Counter baseline
            Inputs: synthetic widgets
            Outputs: counts
            Assumptions: toy lab
            Setting: toy lab
            """
        ).strip()
        with tempfile.TemporaryDirectory() as temporary:
            pool = Path(temporary) / "pool.json"
            pool.write_text(
                json.dumps(
                    {
                        "papers": [
                            {
                                "id": "synthetic-prior",
                                "title": "Synthetic Prior Note",
                                "abstract": "A prior method for counting synthetic widgets.",
                                "date": "2024-05-01",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def _transport(*_args, **_kwargs):
                raise AssertionError("pool mode must not call a provider")

            judgment = run_related_work_search(
                text,
                mode="pool",
                pool_path=pool,
                transport=_transport,
            )

        self.assertEqual([query.id for query in judgment.queries], ["Q-pool"])
        self.assertEqual([item.title for item in judgment.candidates], ["Synthetic Prior Note"])
        self.assertEqual(judgment.status, "complete")

    def test_proposal_packet_does_not_ask_for_a_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text(_proposal() + "\n", encoding="utf-8")
            (workspace / "prompt.md").write_text("Audit the proposal.\n", encoding="utf-8")
            config = workspace / "review.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "proposal-packet"
                    title = "Proposal packet"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[routes]]
                    id = "M0"
                    name = "Proposal route"
                    kind = "prompt"
                    prompt = "prompt.md"

                    [[stages]]
                    id = "packet"
                    type = "prompt_packet"
                    [stages.params]
                    route_ids = ["M0"]
                    include_source_text = true
                    input_mode = "proposal"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            manifest = run_pipeline(config)
            packet = (
                workspace / ".runs" / manifest.run_id / "stages" / "packet" / "M0.md"
            ).read_text(encoding="utf-8")

        self.assertIn("- Input mode: `proposal`", packet)
        self.assertIn("## Proposal contributions (audit each one)", packet)
        self.assertIn("Synthetic Widget Counter", packet)
        self.assertNotIn("## Figures (mandatory inspection)", packet)
        self.assertNotIn("PDF Contributions", packet)

    def test_manuscript_packet_keeps_the_pdf_contribution_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text(_proposal() + "\n", encoding="utf-8")
            (workspace / "prompt.md").write_text("Audit the manuscript.\n", encoding="utf-8")
            config = workspace / "review.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "manuscript-packet"
                    title = "Manuscript packet"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[routes]]
                    id = "M0"
                    name = "Manuscript route"
                    kind = "prompt"
                    prompt = "prompt.md"

                    [[stages]]
                    id = "packet"
                    type = "prompt_packet"
                    [stages.params]
                    route_ids = ["M0"]
                    include_source_text = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            manifest = run_pipeline(config)
            packet = (workspace / ".runs" / manifest.run_id / "stages" / "packet" / "M0.md").read_text(
                encoding="utf-8"
            )

        self.assertNotIn("- Input mode: `proposal`", packet)
        self.assertIn("PDF Contributions", packet)
        self.assertNotIn("## Proposal contributions (audit each one)", packet)

    def test_versioned_arxiv_ids_match(self) -> None:
        blocked = work_id_keys("arxiv:2401.01234v2")
        hit = work_id_keys("2401.01234")
        self.assertTrue(blocked & hit)
        self.assertFalse(work_id_keys("s2:abcdv2") & work_id_keys("s2:abcd"))
