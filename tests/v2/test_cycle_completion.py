"""验证 Stage G broker/reconciliation 事实到 cycle 终态的映射。

作用：区分 dry-run、no-action、open、partial、rejection 与 uncertain。
重要性：正常 open/partial 不能被误报为程序失败，uncertain 也不能伪装成功。
"""

from __future__ import annotations

import unittest

from v2.main import _cycle_final_status
from v2.models.state import CycleStatus


class CycleCompletionTests(unittest.TestCase):
    def status(
        self,
        *,
        allow: bool = True,
        submitted: int = 0,
        rejected: int = 0,
        uncertain: int = 0,
        **summary: int,
    ) -> CycleStatus:
        return _cycle_final_status(
            allow_trade=allow,
            submission={
                "submitted_count": submitted,
                "rejected_count": rejected,
                "uncertain_count": uncertain,
                "cancel_requested_count": 0,
            },
            reconciliation={"summary": summary},
        )

    def test_dry_and_no_action(self) -> None:
        self.assertEqual(
            self.status(allow=False),
            CycleStatus.COMPLETED_DRY_RUN,
        )
        self.assertEqual(
            self.status(),
            CycleStatus.COMPLETED_NO_ACTION,
        )

    def test_open_and_partial_are_normal(self) -> None:
        self.assertEqual(
            self.status(open=1),
            CycleStatus.COMPLETED_WITH_OPEN_ORDERS,
        )
        self.assertEqual(
            self.status(partially_filled=1),
            CycleStatus.COMPLETED_WITH_PARTIAL_FILLS,
        )

    def test_rejection_and_uncertain_are_distinct(self) -> None:
        self.assertEqual(
            self.status(rejected=1),
            CycleStatus.COMPLETED_WITH_REJECTIONS,
        )
        self.assertEqual(
            self.status(uncertain=1),
            CycleStatus.BLOCKED_SUBMISSION_UNCERTAIN,
        )


if __name__ == "__main__":
    unittest.main()
