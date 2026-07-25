"""静态验证 Stage G broker 写 API 白名单。

作用：只允许 submit_order 位于 order_submitter，cancel_order_by_id 位于 action executor。
重要性：直接 replace、批量取消和平仓 API 在全部 v2 生产代码中必须为零。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


class StageGWriteWhitelistTests(unittest.TestCase):
    def test_only_two_files_contain_allowed_writes(self) -> None:
        root = Path(__file__).resolve().parents[2] / "src/v2"
        matches: dict[str, set[str]] = {}
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for name in (
                "submit_order",
                "cancel_order_by_id",
            ):
                if re.search(rf"\.\s*{name}\s*\(", source):
                    matches.setdefault(name, set()).add(path.name)
        self.assertEqual(
            matches,
            {
                "submit_order": {"order_submitter.py"},
                "cancel_order_by_id": {
                    "order_action_executor.py"
                },
            },
        )

    def test_forbidden_write_apis_are_absent(self) -> None:
        root = Path(__file__).resolve().parents[2] / "src/v2"
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.rglob("*.py")
        )
        for name in (
            "replace_order_by_id",
            "cancel_orders",
            "close_all_positions",
            "close_position",
        ):
            self.assertIsNone(
                re.search(rf"\.\s*{name}\s*\(", sources),
                name,
            )
        self.assertNotRegex(
            sources, r"\b(?:from|import)\s+v1\b"
        )


if __name__ == "__main__":
    unittest.main()
