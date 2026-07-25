"""验证 Stage G 顺序 submit 成功、拒绝与本地构造失败。

作用：检查写前日志、响应立即落盘、SDK request 和明确 4xx 分类。
重要性：只有 validated approved spec 可以触发唯一一次 paper submit。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.submission import SubmissionOperationState
from v2.trading.order_submitter import submit_approved_order
from tests.v2.submission_support import (
    FakeAPIError,
    WriteTradingClient,
    clients_for,
    journal_for,
    operation,
    request_spec,
)


class OrderSubmitterTests(unittest.TestCase):
    def test_submit_success_persists_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient()
            item = operation()
            journal = journal_for(Path(temporary), item)
            result = submit_approved_order(
                clients=clients_for(trading),
                spec=request_spec(),
                operation=item,
                journal=journal,
            )
            self.assertEqual(trading.submit_calls, 1)
            self.assertEqual(
                result.state,
                SubmissionOperationState.COMPLETED,
            )
            self.assertEqual(result.broker_status, "new")

    def test_clear_rejection_is_definite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient(
                submit_error=FakeAPIError(
                    "rejected", status_code=422
                )
            )
            item = operation()
            journal = journal_for(Path(temporary), item)
            result = submit_approved_order(
                clients=clients_for(trading),
                spec=request_spec(),
                operation=item,
                journal=journal,
            )
            self.assertEqual(
                result.state,
                SubmissionOperationState.FAILED_DEFINITE,
            )
            self.assertEqual(trading.submit_calls, 1)

    def test_invalid_local_spec_never_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient()
            item = operation()
            journal = journal_for(Path(temporary), item)
            spec = request_spec()
            spec["local_sdk_validated"] = False
            result = submit_approved_order(
                clients=clients_for(trading),
                spec=spec,
                operation=item,
                journal=journal,
            )
            self.assertEqual(trading.submit_calls, 0)
            self.assertEqual(
                result.state,
                SubmissionOperationState.FAILED_DEFINITE,
            )

    def test_fresh_capacity_failure_never_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trading = WriteTradingClient()
            item = operation()
            result = submit_approved_order(
                clients=clients_for(trading),
                spec=request_spec(),
                operation=item,
                journal=journal_for(Path(temporary), item),
                write_preflight=lambda: (
                    (_ for _ in ()).throw(
                        ValueError("buying power changed")
                    )
                ),
            )
            self.assertEqual(trading.submit_calls, 0)
            self.assertEqual(
                result.state,
                SubmissionOperationState.FAILED_DEFINITE,
            )


if __name__ == "__main__":
    unittest.main()
