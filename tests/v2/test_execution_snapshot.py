"""验证组合决策之后的 Stage E 执行级事实快照。

作用：覆盖账户、订单、报价、成交、资产能力与严格时间顺序。
重要性：执行代理不得依赖组合阶段之前的旧行情。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
