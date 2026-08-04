"""验证净外部现金流校正后的日度时间加权收益。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from v2.data.alpaca_client import AlpacaClients
from v2.data.performance import build_performance_summary


class _Trading:
    def __init__(self, activities: list[dict]) -> None:
        self.activities = activities

    def get_portfolio_history(self, request: object) -> object:
        del request
        timestamps = [
            int(datetime(2026, 7, day, tzinfo=timezone.utc).timestamp())
            for day in (9, 10, 11)
        ]
        return SimpleNamespace(timestamp=timestamps, equity=["100", "160", "180"])

    def get(self, path: str, data: dict) -> list[dict]:
        self.last_request = (path, data)
        return self.activities


class PerformanceTests(unittest.TestCase):
    def test_links_returns_after_deposits(self) -> None:
        trading = _Trading(
            [
                {
                    "id": "not-persisted-1",
                    "activity_type": "OCT",
                    "description": "Deposit Transaction",
                    "qty": "100",
                    "price": "1",
                    "transaction_time": "2026-07-08T08:00:00-04:00",
                },
                {
                    "id": "not-persisted-2",
                    "activity_type": "CSD",
                    "description": "Cash deposit",
                    "net_amount": "50",
                    "transaction_time": "2026-07-09T08:00:00-04:00",
                },
            ]
        )
        summary = build_performance_summary(
            clients=AlpacaClients(
                trading=trading,
                stock_data=object(),
                paper=False,
            ),
            run_date="2026-07-10",
            current_equity="180",
        )
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["net_contributions_total"], "150")
        self.assertEqual(summary["net_profit_after_contributions"], "30")
        self.assertAlmostEqual(float(summary["daily_twr"]), 0.125)
        self.assertAlmostEqual(float(summary["cumulative_twr"]), 0.2)
        serialized = str(summary)
        self.assertNotIn("not-persisted", serialized)
        self.assertNotIn("Deposit Transaction", serialized)

    def test_intraday_flow_is_explicitly_partial(self) -> None:
        trading = _Trading(
            [
                {
                    "activity_type": "OCT",
                    "description": "Deposit Transaction",
                    "qty": "50",
                    "price": "1",
                    "transaction_time": "2026-07-09T13:00:00-04:00",
                }
            ]
        )
        summary = build_performance_summary(
            clients=AlpacaClients(
                trading=trading,
                stock_data=object(),
                paper=False,
            ),
            run_date="2026-07-10",
            current_equity="180",
        )
        self.assertEqual(summary["status"], "partial")
        self.assertIn(
            "INTRADAY_FLOW_TIMING_APPROXIMATED",
            {item["code"] for item in summary["warnings"]},
        )


if __name__ == "__main__":
    unittest.main()
