"""验证纽约市场时段、隔夜交易日映射和分钟线摘要。

作用：覆盖 regular、extended、overnight、周末与假日的边界。
重要性：错误时段会误阻止有效 paper 决策或在闭市时错误放行订单。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from v2.data.intraday import (
    determine_market_phase,
    market_session_date,
    summarize_intraday,
)


NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


class MarketPhaseTests(unittest.TestCase):
    def test_session_boundaries(self) -> None:
        cases = (
            (3, 0, "overnight_session"),
            (8, 0, "before_market_open"),
            (10, 0, "regular_session"),
            (18, 0, "after_market_close"),
            (21, 0, "overnight_session"),
        )
        for hour, minute, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    determine_market_phase(
                        datetime(
                            2026,
                            7,
                            23,
                            hour,
                            minute,
                            tzinfo=NY,
                        )
                    ),
                    expected,
                )

    def test_weekend_and_holiday(self) -> None:
        cases = (
            (
                datetime(
                    2026, 7, 25, 10, tzinfo=NY
                ),
                "market_closed_weekend",
            ),
            (
                datetime(
                    2026, 7, 26, 19, 59, tzinfo=NY
                ),
                "market_closed_weekend",
            ),
            (
                datetime(
                    2026, 7, 26, 20, 0, tzinfo=NY
                ),
                "overnight_session",
            ),
            (
                datetime(
                    2026, 7, 31, 3, 59, tzinfo=NY
                ),
                "overnight_session",
            ),
            (
                datetime(
                    2026, 7, 31, 20, 0, tzinfo=NY
                ),
                "market_closed_weekend",
            ),
        )
        for value, expected in cases:
            with self.subTest(
                value=value,
                expected=expected,
            ):
                self.assertEqual(
                    determine_market_phase(value),
                    expected,
                )
        self.assertEqual(
            determine_market_phase(
                datetime(
                    2026,
                    7,
                    23,
                    10,
                    tzinfo=NY,
                ),
                is_market_holiday=True,
            ),
            "market_closed_holiday",
        )
        self.assertEqual(
            determine_market_phase(
                datetime(
                    2026,
                    7,
                    26,
                    22,
                    tzinfo=NY,
                ),
                is_market_holiday=True,
            ),
            "market_closed_holiday",
        )
        self.assertEqual(
            market_session_date(
                datetime(
                    2026,
                    7,
                    26,
                    22,
                    tzinfo=NY,
                )
            ).isoformat(),
            "2026-07-27",
        )

    def test_intraday_summary(self) -> None:
        start = datetime(
            2026,
            7,
            23,
            13,
            30,
            tzinfo=UTC,
        )
        bars = [
            SimpleNamespace(
                timestamp=(
                    start
                    + timedelta(minutes=index)
                ),
                open=100 + index * 0.1,
                high=101 + index * 0.1,
                low=99 + index * 0.1,
                close=100.5 + index * 0.1,
                volume=1000 + index,
                trade_count=10,
                vwap=100.2 + index * 0.1,
            )
            for index in range(61)
        ]
        result = summarize_intraday(
            "MU",
            bars,
            market_phase="regular_session",
            requested_window=60,
        )
        self.assertEqual(
            result["window_status"],
            "complete",
        )
        self.assertIsNotNone(
            result["summary"][
                "window_changes_percent"
            ]["60m"]
        )


if __name__ == "__main__":
    unittest.main()
