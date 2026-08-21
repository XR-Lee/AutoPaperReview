from __future__ import annotations

import unittest

from pydantic import ValidationError

from autopaperreview.config import HarnessConfig


def config_with_stages(stages: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "project": {
            "id": "dag-test",
            "title": "DAG test",
            "source": "manuscript.txt",
        },
        "stages": stages,
    }


class DagValidationTests(unittest.TestCase):
    def test_rejects_unknown_dependency(self) -> None:
        raw = config_with_stages(
            [{"id": "report", "type": "report", "depends_on": ["missing"]}]
        )
        with self.assertRaisesRegex(ValidationError, "depends on unknown stages"):
            HarnessConfig.model_validate(raw)

    def test_rejects_self_dependency(self) -> None:
        raw = config_with_stages(
            [{"id": "report", "type": "report", "depends_on": ["report"]}]
        )
        with self.assertRaisesRegex(ValidationError, "cannot depend on itself"):
            HarnessConfig.model_validate(raw)

    def test_rejects_multi_stage_cycle(self) -> None:
        raw = config_with_stages(
            [
                {"id": "one", "type": "ingest", "depends_on": ["three"]},
                {"id": "two", "type": "consensus", "depends_on": ["one"]},
                {"id": "three", "type": "report", "depends_on": ["two"]},
            ]
        )
        with self.assertRaisesRegex(ValidationError, "stage graph contains a cycle"):
            HarnessConfig.model_validate(raw)

    def test_ordering_rejects_enabled_stage_blocked_by_disabled_dependency(self) -> None:
        config = HarnessConfig.model_validate(
            config_with_stages(
                [
                    {"id": "prepare", "type": "ingest", "enabled": False},
                    {"id": "report", "type": "report", "depends_on": ["prepare"]},
                ]
            )
        )
        with self.assertRaisesRegex(ValueError, "blocked by disabled dependencies"):
            config.ordered_stages()

    def test_orders_valid_graph_topologically(self) -> None:
        config = HarnessConfig.model_validate(
            config_with_stages(
                [
                    {"id": "report", "type": "report", "depends_on": ["consensus"]},
                    {"id": "ingest", "type": "ingest"},
                    {"id": "consensus", "type": "consensus", "depends_on": ["ingest"]},
                ]
            )
        )
        self.assertEqual([stage.id for stage in config.ordered_stages()], ["ingest", "consensus", "report"])


if __name__ == "__main__":
    unittest.main()
