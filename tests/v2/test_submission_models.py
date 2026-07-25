"""验证 Stage G submission 模型与 Alpaca 状态分类。

作用：覆盖序列化、状态枚举、脱敏错误与 broker submission 计数。
重要性：错误分类会直接影响是否允许恢复或停止后续写操作。
"""

from __future__ import annotations

import unittest

from v2.models.submission import (
    ACTIVE_ORDER_STATUSES,
    BROKER_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    SubmissionIntent,
    SubmissionOperationState,
    broker_submission_document,
    sanitized_error,
)
from v2.main import _revise_submission_intent
from tests.v2.submission_support import operation


class SubmissionModelTests(unittest.TestCase):
    def test_all_documented_order_statuses_are_known(
        self,
    ) -> None:
        self.assertEqual(len(BROKER_ORDER_STATUSES), 16)
        self.assertIn("pending_cancel", ACTIVE_ORDER_STATUSES)
        self.assertNotIn(
            "pending_cancel", TERMINAL_ORDER_STATUSES
        )
        self.assertIn("filled", TERMINAL_ORDER_STATUSES)

    def test_intent_serializes_hashes_and_counts(self) -> None:
        intent = SubmissionIntent(
            profile_id="paper1",
            environment="paper",
            run_date="2026-07-24",
            cycle_id="20260724T140000",
            allow_trade=True,
            validated_orders_hash="a" * 64,
            request_specs_hash="b" * 64,
            action_plan_hash="c" * 64,
            submission_policy="alpaca_paper@1.0.0",
            submission_policy_hash="d" * 64,
            approved_plan_ids=("plan-1",),
            dependent_plan_ids=(),
            cancel_action_ids=(),
            expected_write_count=1,
        ).to_dict()
        self.assertEqual(intent["status"], "prepared")
        self.assertEqual(intent["expected_write_count"], 1)
        self.assertEqual(intent["intent_revision"], 1)
        self.assertEqual(intent["prior_revisions"], [])

    def test_intent_revision_preserves_prior_hashes(
        self,
    ) -> None:
        original = SubmissionIntent(
            profile_id="paper1",
            environment="paper",
            run_date="2026-07-24",
            cycle_id="20260724T140000",
            allow_trade=True,
            validated_orders_hash="a" * 64,
            request_specs_hash="b" * 64,
            action_plan_hash="c" * 64,
            submission_policy="alpaca_paper@1.0.0",
            submission_policy_hash="d" * 64,
            approved_plan_ids=(),
            dependent_plan_ids=("plan-1",),
            cancel_action_ids=("cancel-1",),
            expected_write_count=1,
        ).to_dict()
        replacement = dict(original)
        replacement["validated_orders_hash"] = "e" * 64
        replacement["dependent_plan_ids"] = []
        replacement["approved_plan_ids"] = ["plan-1"]
        revised = _revise_submission_intent(
            original, replacement
        )
        self.assertEqual(revised["intent_revision"], 2)
        self.assertEqual(
            revised["prior_revisions"][0][
                "validated_orders_hash"
            ],
            "a" * 64,
        )

    def test_broker_document_counts_uncertain(self) -> None:
        item = operation()
        item.state = SubmissionOperationState.UNCERTAIN
        item.error = {"type": "Timeout", "message": "unknown"}
        document = broker_submission_document(
            profile_id="paper1",
            run_date="2026-07-24",
            cycle_id="20260724T140000",
            submission_requested=True,
            submission_performed=True,
            validated_orders_hash="a" * 64,
            operations=[item],
            started_at="2026-07-24T14:00:00+00:00",
        )
        self.assertEqual(document["uncertain_count"], 1)
        self.assertEqual(document["submitted_count"], 0)

    def test_sanitized_error_is_bounded(self) -> None:
        result = sanitized_error(
            RuntimeError(
                "api_key=super-secret " + "x" * 800
            )
        )
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result["message"]), 500)
        self.assertNotIn("super-secret", result["message"])


if __name__ == "__main__":
    unittest.main()
