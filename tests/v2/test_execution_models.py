"""验证 Stage E 执行意图模型只表达研究边界。

作用：检查可执行与不可执行决策集合及禁止字段定义。
重要性：数量、名义金额和成交声明必须留在未来订单阶段之外。
"""

from __future__ import annotations

import unittest

from v2.models.execution import (
    EXECUTABLE_DECISIONS,
    EXECUTION_FORBIDDEN_OUTPUT_FIELDS,
    NON_EXECUTABLE_DECISIONS,
)


class ExecutionModelTests(unittest.TestCase):
    def test_decisions_and_forbidden_fields(
        self,
    ) -> None:
        self.assertEqual(
            EXECUTABLE_DECISIONS,
            {"approve", "modify"},
        )
        self.assertEqual(
            NON_EXECUTABLE_DECISIONS,
            {"defer", "reject", "no_action"},
        )
        self.assertTrue(
            {
                "quantity",
                "notional",
                "broker_order_request",
                "submitted",
                "filled",
            }
            <= EXECUTION_FORBIDDEN_OUTPUT_FIELDS
        )


if __name__ == "__main__":
    unittest.main()
