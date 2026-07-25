"""验证首轮详细日报与后续增量更新。

作用：检查版本身份、cycle 事实、只创建一次及同 cycle 幂等。
重要性：日报是每个交易决策的同日书面备份，不能覆盖既有历史。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from v2.reports.daily_report import update_daily_report


def state(cycle_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        run_date="2026-07-24",
        profile_id="paper1",
        cycle_id=cycle_id,
        cycle_kind=SimpleNamespace(value="execution_refresh"),
        release={
            "app_version": "2.0.0",
            "strategy_id": "core_long",
            "strategy_version": "1.2.0",
            "risk_profile": "paper_standard@1.1.0",
            "order_policy": "paper_equity@1.0.0",
            "submission_policy": "alpaca_paper@1.0.0",
        },
    )


class DailyReportTests(unittest.TestCase):
    def documents(self):
        return (
            {"summary": {"proposed": 1, "approved": 1}},
            {"submitted_count": 1, "existing_count": 0},
            {
                "account": {"account_id_hash": "a" * 64},
                "summary": {
                    "filled": 0,
                    "partially_filled": 0,
                    "open": 1,
                    "rejected": 0,
                    "uncertain": 0,
                    "canceled": 0,
                },
                "capital": {"cash": 1000},
                "requires_next_cycle_rebalance": False,
                "reasons": [],
                "warnings": [],
                "errors": [],
            },
        )

    def test_first_report_is_detailed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.md"
            validated, submission, reconciliation = (
                self.documents()
            )
            created = update_daily_report(
                path,
                state=state("20260724T140000"),
                validated=validated,
                submission=submission,
                reconciliation=reconciliation,
            )
            self.assertTrue(created)
            text = path.read_text(encoding="utf-8")
            self.assertIn("Submission policy", text)
            self.assertIn("## 对账", text)

    def test_later_cycle_appends_and_same_cycle_is_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "daily.md"
            documents = self.documents()
            update_daily_report(
                path,
                state=state("20260724T140000"),
                validated=documents[0],
                submission=documents[1],
                reconciliation=documents[2],
            )
            update_daily_report(
                path,
                state=state("20260724T150000"),
                validated=documents[0],
                submission=documents[1],
                reconciliation=documents[2],
            )
            once = path.read_text(encoding="utf-8")
            update_daily_report(
                path,
                state=state("20260724T150000"),
                validated=documents[0],
                submission=documents[1],
                reconciliation=documents[2],
            )
            self.assertEqual(
                once, path.read_text(encoding="utf-8")
            )
            self.assertIn("更新", once)


if __name__ == "__main__":
    unittest.main()
