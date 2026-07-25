"""验证 cancel 请求前后成交竞态。

作用：确认取消前已成交会跳过写，取消异常后读到 filled 仍以券商终态为准。
重要性：filled 订单不得被误当作已释放暴露并触发 replacement。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.submission import (
    SubmissionOperationState,
    SubmissionOperationType,
)
from v2.trading.order_action_executor import execute_cancel
from tests.v2.fakes import fake_order
from tests.v2.submission_support import (
    WriteTradingClient,
    clients_for,
    journal_for,
    operation,
)


class CancelRaceTests(unittest.TestCase):
    def test_filled_before_cancel_never_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient()
            trading.add_order(
                fake_order(
                    "MU",
                    id="broker-1",
                    status="filled",
                    filled_qty="5",
                    filled_avg_price="100",
                )
            )
            item = operation(
                kind=SubmissionOperationType.CANCEL,
                broker_order_id="broker-1",
            )
            result = execute_cancel(
                clients=clients_for(trading),
                operation=item,
                journal=journal_for(Path(temporary), item),
            )
            self.assertEqual(trading.cancel_calls, 0)
            self.assertEqual(result.filled_quantity, 5)

    def test_cancel_exception_with_fill_is_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient(
                cancel_error=TimeoutError("lost"),
                cancel_statuses=["filled"],
            )
            trading.add_order(
                fake_order(
                    "MU",
                    id="broker-1",
                    filled_qty="5",
                    filled_avg_price="100",
                )
            )
            item = operation(
                kind=SubmissionOperationType.CANCEL,
                broker_order_id="broker-1",
            )
            result = execute_cancel(
                clients=clients_for(trading),
                operation=item,
                journal=journal_for(Path(temporary), item),
            )
            self.assertEqual(
                result.state,
                SubmissionOperationState.COMPLETED,
            )
            self.assertEqual(result.broker_status, "filled")


if __name__ == "__main__":
    unittest.main()
