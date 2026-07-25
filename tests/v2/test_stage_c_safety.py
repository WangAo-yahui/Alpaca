"""保持 Stage C 模块无券商写调用，同时允许 Stage G 两个白名单执行器。

作用：扫描除 paper 写白名单之外的全部 v2 源文件。
重要性：Stage G 的新增能力不能倒灌到早期数据、决策或编排模块。
"""

from __future__ import annotations

import unittest
from pathlib import Path


class StageCSafetyTests(unittest.TestCase):
    def test_v2_has_no_order_submission_calls(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        whitelist = {
            "order_submitter.py",
            "order_action_executor.py",
        }
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (root / "src" / "v2").rglob("*.py")
            if path.name not in whitelist
        )
        self.assertNotIn(
            "submit_order(",
            source,
        )
        self.assertNotIn(
            "place_order(",
            source,
        )
        self.assertNotIn(
            "from v1",
            source,
        )
        self.assertNotIn(
            "import v1",
            source,
        )


if __name__ == "__main__":
    unittest.main()
