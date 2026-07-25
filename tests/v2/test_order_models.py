"""验证 Stage F Decimal 订单模型。

作用：检查金额、价格和数量序列化为十进制字符串。
重要性：杜绝二进制浮点超买，并保持提交事实永远为零。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.trading.order_builder import build_order_plan
from tests.v2.order_support import (
    execution_output,
    order_configs,
    order_paths,
    order_state,
    portfolio_output,
    snapshot,
)


class OrderModelTests(unittest.TestCase):
    def test_plan_serializes_decimal_strings(self) -> None:
        risk, policy = order_configs()
        with tempfile.TemporaryDirectory() as temp:
            plan = build_order_plan(
                paths=order_paths(Path(temp)),
                state=order_state(),
                execution_output=execution_output(),
                pretrade_snapshot=snapshot(),
                portfolio_output=portfolio_output(),
                risk_profile=risk,
                order_policy=policy,
            )
        payload = plan.to_dict()
        order = payload["orders"][0]
        self.assertIsInstance(
            order["quantity"],
            str,
        )
        self.assertIsInstance(
            order["planned_value"],
            str,
        )
        self.assertFalse(
            payload["submission_performed"]
        )
        self.assertEqual(
            payload["submitted_order_count"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
