from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from autopaperreview.models import ReviewPackage
from autopaperreview.pipeline import run_pipeline


def chinese_issue(title: str) -> dict:
    return {
        "id": "I001",
        "severity": "major",
        "category": "方法",
        "title_zh": title,
        "location": "结果",
        "evidence_zh": "稿件中的证据。",
        "impact_zh": "影响尚未解决。",
        "required_action_zh": "补充验证。",
        "confidence": 0.8,
    }


class ConsensusTests(unittest.TestCase):
    def test_same_legacy_id_with_distinct_chinese_titles_stays_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text("合成稿件。\n", encoding="utf-8")
            (workspace / "left.json").write_text(
                json.dumps([chinese_issue("训练测试数据存在泄漏")], ensure_ascii=False),
                encoding="utf-8",
            )
            (workspace / "right.json").write_text(
                json.dumps([chinese_issue("统计结果缺少不确定性")], ensure_ascii=False),
                encoding="utf-8",
            )
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "unicode-consensus"
                    title = "Unicode consensus"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "left"
                    type = "import_issues"
                    [stages.params]
                    path = "left.json"

                    [[stages]]
                    id = "right"
                    type = "import_issues"
                    [stages.params]
                    path = "right.json"

                    [[stages]]
                    id = "consensus"
                    type = "consensus"
                    depends_on = ["left", "right"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            manifest = run_pipeline(config_path)
            consensus_path = (
                workspace / ".runs" / manifest.run_id / "stages" / "consensus" / "consensus.json"
            )
            package = ReviewPackage.model_validate_json(consensus_path.read_text(encoding="utf-8"))

            self.assertEqual(len(package.issues), 2)
            self.assertEqual(len({issue.id for issue in package.issues}), 2)
            self.assertEqual(
                {issue.title.primary for issue in package.issues},
                {"训练测试数据存在泄漏", "统计结果缺少不确定性"},
            )
            self.assertIn("I001", {issue.id for issue in package.issues})
            self.assertTrue(any(issue.id.startswith("I001-") for issue in package.issues))


if __name__ == "__main__":
    unittest.main()
