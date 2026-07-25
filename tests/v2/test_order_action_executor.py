"""验证 Stage G cancel 的查询、写前日志、轮询和终态确认。

作用：覆盖成功取消、已终态跳过、异常不确定和 pending_cancel。
重要性：取消未确认前不得释放资金或提交 replacement。
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


class OrderActionExecutorTests(unittest.TestCase):
    def test_cancel_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient(
                cancel_statuses=["new", "canceled"]
            )
            target = fake_order(
                "MU",
                id="broker-1",
                client_order_id="old-1",
                filled_avg_price=None,
            )
            trading.add_order(target)
            item = operation(
                kind=SubmissionOperationType.CANCEL,
                broker_order_id="broker-1",
            )
            result = execute_cancel(
                clients=clients_for(trading),
                operation=item,
                journal=journal_for(Path(temporary), item),
                interval_seconds=0,
                sleeper=lambda _: None,
            )
            self.assertEqual(trading.cancel_calls, 1)
            self.assertEqual(
                result.state,
                SubmissionOperationState.COMPLETED,
            )
            self.assertEqual(result.broker_status, "canceled")

    def test_terminal_order_skips_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient()
            trading.add_order(
                fake_order(
                    "MU",
                    id="broker-1",
                    status="filled",
                    filled_avg_price="101",
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
            self.assertEqual(result.broker_status, "filled")

    def test_pending_cancel_is_not_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient(
                cancel_statuses=[
                    "new",
                    "pending_cancel",
                    "pending_cancel",
                ]
            )
            trading.add_order(
                fake_order(
                    "MU",
                    id="broker-1",
                    filled_avg_price=None,
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
                maximum_seconds=0,
                interval_seconds=0,
                sleeper=lambda _: None,
            )
            self.assertEqual(
                result.state,
                SubmissionOperationState.UNCERTAIN,
            )

    def test_response_received_resume_does_not_cancel_again(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient(
                cancel_statuses=["canceled"]
            )
            trading.add_order(
                fake_order(
                    "MU",
                    id="broker-1",
                    filled_avg_price=None,
                )
            )
            trading.cancel_calls = 1
            item = operation(
                kind=SubmissionOperationType.CANCEL,
                state=(
                    SubmissionOperationState.RESPONSE_RECEIVED
                ),
                broker_order_id="broker-1",
            )
            result = execute_cancel(
                clients=clients_for(trading),
                operation=item,
                journal=journal_for(Path(temporary), item),
                sleeper=lambda _: None,
            )
            self.assertEqual(trading.cancel_calls, 1)
            self.assertIn(
                result.state,
                {
                    SubmissionOperationState.SKIPPED,
                    SubmissionOperationState.COMPLETED,
                },
            )


if __name__ == "__main__":
    unittest.main()
