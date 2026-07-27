"""验证组合决策之后的 Stage E 执行级事实快照。

作用：覆盖账户、订单、报价、成交、资产能力与严格时间顺序。
重要性：执行代理不得依赖组合阶段之前的旧行情。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from alpaca.data.enums import DataFeed

from v2.data.execution_snapshot import (
    create_execution_snapshot,
)
from v2.runtime import build_cycle_paths
from tests.v2.support import stage_d_clients


class ExecutionSnapshotTests(unittest.TestCase):
    def test_snapshot_is_later_and_contains_execution_facts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = build_cycle_paths(
                run_date="2026-07-23",
                cycle_id="20260723T100000",
                project_root=Path(temp),
                profile_id="paper1",
                strategy_id="core_long",
                strategy_version="1.2.0",
            )
            portfolio = {
                "generated_at": (
                    "2026-07-23T13:59:59+00:00"
                ),
                "decisions": [
                    {
                        "symbol": "S000",
                    }
                ],
            }
            result = create_execution_snapshot(
                paths,
                stage_d_clients(),
                portfolio_output=portfolio,
                now=datetime(
                    2026,
                    7,
                    23,
                    14,
                    tzinfo=timezone.utc,
                ),
                is_market_holiday=False,
            )
            self.assertGreater(
                result.payload["retrieved_at"],
                portfolio["generated_at"],
            )
            for key in (
                "account",
                "positions",
                "open_orders",
                "today_orders",
                "quotes",
                "latest_trades",
                "intraday",
                "assets",
                "broker_extended_hours_capability",
            ):
                self.assertIn(key, result.payload)
            self.assertTrue(result.execution_ready)

    def test_sunday_overnight_uses_overnight_feed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = build_cycle_paths(
                run_date="2026-07-26",
                cycle_id="20260726T220000",
                project_root=Path(temp),
                profile_id="paper1",
                strategy_id="core_long",
                strategy_version="1.2.0",
            )
            clients = stage_d_clients(
                positions=[],
                orders=[],
            )
            result = create_execution_snapshot(
                paths,
                clients,
                portfolio_output={
                    "generated_at": (
                        "2026-07-27T01:59:59+00:00"
                    ),
                    "decisions": [{"symbol": "S000"}],
                },
                now=datetime(
                    2026,
                    7,
                    27,
                    2,
                    tzinfo=timezone.utc,
                ),
                is_market_holiday=False,
            )
        self.assertEqual(
            result.payload["market_phase"],
            "overnight_session",
        )
        capability = result.payload[
            "broker_extended_hours_capability"
        ]
        self.assertIn(
            "overnight_session",
            capability["supported_phases"],
        )
        stock_data = clients.stock_data
        self.assertEqual(
            stock_data.quote_requests[-1].feed,
            DataFeed.OVERNIGHT,
        )
        self.assertEqual(
            stock_data.trade_requests[-1].feed,
            DataFeed.OVERNIGHT,
        )
        self.assertEqual(
            stock_data.bar_requests[-1].feed,
            DataFeed.OVERNIGHT,
        )


if __name__ == "__main__":
    unittest.main()
