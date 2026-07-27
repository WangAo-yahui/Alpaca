"""验证 Stage F 最终订单前快照。

作用：检查快照时序、Decimal 字符串和关键账户刷新失败。
重要性：旧 execution 快照不得被误当成可提交订单的最新事实。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from alpaca.data.enums import DataFeed

from v2.data.alpaca_client import AlpacaClients
from v2.data.pretrade_snapshot import (
    create_pretrade_snapshot,
)
from tests.v2.fakes import (
    FakeStockDataClient,
    FakeTradingClient,
    fake_position,
)
from tests.v2.order_support import (
    GENERATED_AT,
    execution_output,
    order_configs,
    order_paths,
)


class PreTradeSnapshotTests(unittest.TestCase):
    def _clients(self, *, failures=None):
        quote = SimpleNamespace(
            bid_price="100.00",
            ask_price="100.10",
            bid_size="10",
            ask_size="10",
            timestamp=GENERATED_AT,
        )
        return AlpacaClients(
            trading=FakeTradingClient(
                positions=[
                    fake_position("MU")
                ],
                failures=failures,
            ),
            stock_data=FakeStockDataClient(
                quotes={"MU": quote}
            ),
        )

    def test_snapshot_is_after_execution_and_decimalized(
        self,
    ) -> None:
        _, policy = order_configs()
        with tempfile.TemporaryDirectory() as temp:
            paths = order_paths(Path(temp))
            result = create_pretrade_snapshot(
                paths,
                self._clients(),
                execution_output=execution_output(),
                order_policy=policy,
                now=(
                    GENERATED_AT
                    + timedelta(seconds=1)
                ),
                is_market_holiday=False,
            )
            self.assertTrue(
                paths.pretrade_snapshot.is_file()
            )
        self.assertTrue(
            result.order_planning_ready
        )
        self.assertIsInstance(
            result.payload["account"]["cash"],
            str,
        )
        self.assertIsInstance(
            result.payload["quotes"]["MU"][
                "ask_price"
            ],
            str,
        )
        self.assertGreater(
            result.payload["retrieved_at"],
            result.payload[
                "execution_generated_at"
            ],
        )

    def test_critical_account_failure_blocks_planning(
        self,
    ) -> None:
        _, policy = order_configs()
        with tempfile.TemporaryDirectory() as temp:
            result = create_pretrade_snapshot(
                order_paths(Path(temp)),
                self._clients(
                    failures={"account"}
                ),
                execution_output=execution_output(),
                order_policy=policy,
                now=(
                    GENERATED_AT
                    + timedelta(seconds=1)
                ),
                is_market_holiday=False,
            )
        self.assertFalse(
            result.order_planning_ready
        )
        self.assertGreater(
            result.payload["data_quality"][
                "critical_error_count"
            ],
            0,
        )

    def test_sunday_overnight_refresh_uses_overnight_feed(
        self,
    ) -> None:
        _, policy = order_configs()
        clients = self._clients()
        with tempfile.TemporaryDirectory() as temp:
            result = create_pretrade_snapshot(
                order_paths(Path(temp)),
                clients,
                execution_output=execution_output(),
                order_policy=policy,
                now=GENERATED_AT.replace(
                    year=2026,
                    month=7,
                    day=27,
                    hour=2,
                ),
                is_market_holiday=False,
            )
        self.assertEqual(
            result.payload["market_phase"],
            "overnight_session",
        )
        self.assertEqual(
            clients.stock_data.quote_requests[-1].feed,
            DataFeed.OVERNIGHT,
        )


if __name__ == "__main__":
    unittest.main()
