"""验证 partial fill 的成交量、均价和再平衡语义。

作用：确认部分成交仍是 open 暴露，并保留 filled/remaining 事实。
重要性：下一轮不得重复提交原完整数量。
"""

from __future__ import annotations

import unittest

from v2.trading.reconciliation import reconcile_submission
from tests.v2.fakes import fake_order
from tests.v2.submission_support import (
    WriteTradingClient,
    clients_for,
    operation,
)


class PartialFillReconciliationTests(unittest.TestCase):
    def test_partial_fill_is_open_and_rebalance(self) -> None:
        trading = WriteTradingClient()
        trading.add_order(
            fake_order(
                "MU",
                id="broker-1",
                client_order_id="wa2-paper1-test-1",
                status="partially_filled",
                qty="5",
                filled_qty="2",
                filled_avg_price="99.5",
            )
        )
        item = operation()
        item.broker_order_id = "broker-1"
        result = reconcile_submission(
            clients=clients_for(trading),
            profile_id="paper1",
            cycle_id="20260724T140000",
            operations=[item],
        )
        self.assertEqual(
            result["summary"]["partially_filled"], 1
        )
        self.assertEqual(result["summary"]["open"], 1)
        self.assertEqual(
            result["tracked_orders"][0][
                "average_fill_price"
            ],
            99.5,
        )
        self.assertTrue(
            result["requires_next_cycle_rebalance"]
        )


if __name__ == "__main__":
    unittest.main()
