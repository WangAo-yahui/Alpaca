"""验证 submit 响应丢失后的 client_order_id 查询策略。

作用：覆盖超时后已受理与无法确认两条路径。
重要性：网络异常绝不能触发 blind retry 或重复订单。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.submission import SubmissionOperationState
from v2.trading.order_submitter import submit_approved_order
from tests.v2.submission_support import (
    WriteTradingClient,
    clients_for,
    journal_for,
    operation,
    request_spec,
)


class OrderSubmitTimeoutTests(unittest.TestCase):
    def test_timeout_but_lookup_finds_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient(
                submit_error=TimeoutError("response lost"),
                submit_accepts_before_error=True,
            )
            item = operation()
            result = submit_approved_order(
                clients=clients_for(trading),
                spec=request_spec(),
                operation=item,
                journal=journal_for(Path(temporary), item),
            )
            self.assertEqual(
                result.state,
                SubmissionOperationState.LOOKUP_CONFIRMED,
            )
            self.assertEqual(trading.submit_calls, 1)

    def test_timeout_not_found_is_uncertain_no_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient(
                submit_error=TimeoutError("response lost")
            )
            item = operation()
            journal = journal_for(Path(temporary), item)
            first = submit_approved_order(
                clients=clients_for(trading),
                spec=request_spec(),
                operation=item,
                journal=journal,
            )
            second = submit_approved_order(
                clients=clients_for(trading),
                spec=request_spec(),
                operation=first,
                journal=journal,
            )
            self.assertEqual(
                second.state,
                SubmissionOperationState.UNCERTAIN,
            )
            self.assertEqual(trading.submit_calls, 1)


if __name__ == "__main__":
    unittest.main()
