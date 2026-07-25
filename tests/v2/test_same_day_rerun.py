"""验证同日后续 Stage G cycle 的报告与状态语义。

作用：确认日报保留首轮并追加 execution_refresh 更新。
重要性：same-day rerun 不得重写第一轮审计记录或复用已完成 submission。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.reports.daily_report import update_daily_report
from tests.v2.test_daily_report import state


class SameDayRerunTests(unittest.TestCase):
    def test_two_cycles_are_both_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.md"
            validated = {"summary": {}}
            submission = {}
            reconciliation = {
                "account": {},
                "summary": {},
                "capital": {},
                "reasons": [],
                "warnings": [],
                "errors": [],
            }
            for cycle_id in (
                "20260724T140000",
                "20260724T150000",
            ):
                update_daily_report(
                    path,
                    state=state(cycle_id),
                    validated=validated,
                    submission=submission,
                    reconciliation=reconciliation,
                )
            text = path.read_text(encoding="utf-8")
            self.assertEqual(
                text.count("20260724T140000"), 1
            )
            self.assertEqual(
                text.count("20260724T150000"), 1
            )


if __name__ == "__main__":
    unittest.main()
