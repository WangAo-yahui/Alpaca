"""静态验证 Stage F 生产安全边界。

作用：扫描券商写调用、v1 导入和 order_submitter 文件。
重要性：防止未来重构意外把 Stage G 能力提前接入当前可达流程。
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
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.rglob("*.py")
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
        self.assertFalse(
            (
                root
                / "trading"
                / "order_submitter.py"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
