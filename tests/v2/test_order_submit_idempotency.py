"""验证 stable client_order_id 与 journal 恢复不会重复下单。

作用：覆盖券商已有订单和 request_started 中断恢复。
重要性：幂等查询是 Stage G 对网络 exactly-once 不可保证的核心补偿。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.submission import SubmissionOperationState
from v2.trading.order_submitter import submit_approved_order
from tests.v2.fakes import fake_order
from tests.v2.submission_support import (
    WriteTradingClient,
    clients_for,
    journal_for,
    operation,
    request_spec,
)


class OrderSubmitIdempotencyTests(unittest.TestCase):
    def test_existing_client_id_is_not_submitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient()
            trading.add_order(
                fake_order(
                    "MU",
                    client_order_id="wa2-paper1-test-1",
                    id="existing-order",
                    filled_avg_price=None,
                )
            )
            item = operation()
            result = submit_approved_order(
                clients=clients_for(trading),
                spec=request_spec(),
                operation=item,
                journal=journal_for(Path(temporary), item),
            )
            self.assertEqual(trading.submit_calls, 0)
            self.assertEqual(
                result.state,
                SubmissionOperationState.LOOKUP_CONFIRMED,
            )

    def test_request_started_without_order_becomes_uncertain(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient()
            item = operation(
                state=SubmissionOperationState.REQUEST_STARTED
            )
            item.attempt_count = 1
            journal = journal_for(Path(temporary), item)
            result = submit_approved_order(
                clients=clients_for(trading),
                spec=request_spec(),
                operation=item,
                journal=journal,
            )
            self.assertEqual(trading.submit_calls, 0)
            self.assertEqual(
                result.state,
                SubmissionOperationState.UNCERTAIN,
            )


if __name__ == "__main__":
    unittest.main()
