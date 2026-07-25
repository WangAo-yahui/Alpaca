"""验证 cancel-confirm-refresh-revalidate 的 replacement 解锁条件。

作用：检查 canceled 可解锁，filled、partial、pending 与 uncertain 均阻止。
重要性：Stage G 禁用 direct replace，依赖错误不能扩大订单或重复暴露。
"""

from __future__ import annotations

import unittest

from v2.main import _submission_operations
from v2.models.submission import SubmissionOperationState
from v2.trading.order_action_executor import replacement_is_unlocked
from tests.v2.submission_support import operation


class ReplacementDependencyTests(unittest.TestCase):
    def test_confirmed_cancel_unlocks(self) -> None:
        item = operation()
        item.state = SubmissionOperationState.COMPLETED
        item.broker_status = "canceled"
        self.assertTrue(replacement_is_unlocked(item))

    def test_fill_and_partial_do_not_unlock(self) -> None:
        for status in ("filled", "partially_filled"):
            item = operation()
            item.state = SubmissionOperationState.COMPLETED
            item.broker_status = status
            self.assertFalse(replacement_is_unlocked(item))

    def test_uncertain_does_not_unlock(self) -> None:
        item = operation()
        item.state = SubmissionOperationState.UNCERTAIN
        item.error = {"type": "Timeout", "message": "unknown"}
        item.broker_status = "pending_cancel"
        self.assertFalse(replacement_is_unlocked(item))

    def test_replace_intent_prepares_cancel_not_direct_replace(
        self,
    ) -> None:
        operations = _submission_operations(
            validated={"orders": []},
            request_specs={"requests": []},
            action_plan={
                "actions": [
                    {
                        "action_id": "action-1",
                        "action": "replace",
                        "status": "dependent",
                        "broker_order_id": "broker-1",
                        "client_order_id": "old-client",
                        "symbol": "MU",
                    }
                ]
            },
            allow_trade=True,
        )
        self.assertEqual(len(operations), 1)
        self.assertEqual(
            operations[0].operation_type.value,
            "cancel",
        )
        self.assertEqual(
            operations[0].request_summary[
                "source_intent"
            ],
            "replace",
        )


if __name__ == "__main__":
    unittest.main()
