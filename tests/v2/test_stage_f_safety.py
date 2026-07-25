"""静态验证 Stage F 决策层与 Stage G 写白名单边界。

作用：扫描券商写调用、v1 导入，并确认写能力只位于两个指定执行器。
重要性：防止未来重构把 Stage G 写调用扩散到规划、校验、对账或报告模块。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


class StageFSafetyTests(unittest.TestCase):
    def test_no_broker_write_calls_or_v1_imports(
        self,
    ) -> None:
        root = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "v2"
        )
        whitelist = {
            root / "trading" / "order_submitter.py",
            root / "trading" / "order_action_executor.py",
        }
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.rglob("*.py")
            if path not in whitelist
        )
        for name in (
            "submit_order",
            "cancel_order",
            "replace_order",
            "close_position",
        ):
            self.assertIsNone(
                re.search(
                    rf"\.\s*{name}\s*\(",
                    sources,
                ),
                name,
            )
        self.assertNotRegex(
            sources,
            r"\b(?:from|import)\s+v1\b",
        )
        self.assertTrue(all(path.is_file() for path in whitelist))


if __name__ == "__main__":
    unittest.main()
