"""验证 Stage F 挂单动作与依赖计划。

作用：检查 cancel 仍占用资金、replace 依赖刷新、缺失订单被阻止。
重要性：Stage F 不能假定 Stage G 的券商动作已经成功。
"""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from v2.models.orders import OrderStatus
from v2.trading.order_builder import build_order_plan
from tests.v2.order_support import (
    execution_decision,
    execution_output,
    order_configs,
    order_paths,
    order_state,
    portfolio_output,
    snapshot,
)
from tests.v2.test_order_builder import (
    _open_order,
)


class OrderActionPlanTests(unittest.TestCase):
    def _plan(self, action: str):
        risk, policy = order_configs()
        open_order = _open_order(
            side="buy",
            quantity="20",
        )
        with tempfile.TemporaryDirectory() as temp:
            return build_order_plan(
                paths=order_paths(Path(temp)),
                state=order_state(),
                execution_output=execution_output(
                    execution_decision(),
                    actions=[
                        {
                            "order_reference": (
                                open_order[
                                    "broker_order_id"
                                ]
                            ),
                            "symbol": "MU",
                            "action": action,
                            "reason": "test",
                        }
                    ],
                ),
                pretrade_snapshot=snapshot(
                    open_orders=[open_order]
                ),
                portfolio_output=portfolio_output(),
                risk_profile=risk,
                order_policy=policy,
            )

    def test_cancel_keeps_existing_exposure_occupied(
        self,
    ) -> None:
        plan = self._plan("cancel")
        self.assertEqual(
            plan.orders[0]
            .open_buy_remaining_value,
            Decimal("2000"),
        )
        self.assertEqual(
            plan.actions[0].status,
            OrderStatus.DEPENDENT,
        )

    def test_replace_order_is_dependent(self) -> None:
        plan = self._plan("replace")
        self.assertEqual(
            plan.orders[0].status,
            OrderStatus.DEPENDENT,
        )
        self.assertTrue(
            plan.orders[0].depends_on
        )

    def test_missing_order_reference_is_blocked(
        self,
    ) -> None:
        risk, policy = order_configs()
        with tempfile.TemporaryDirectory() as temp:
            plan = build_order_plan(
                paths=order_paths(Path(temp)),
                state=order_state(),
                execution_output=execution_output(
                    execution_decision(),
                    actions=[
                        {
                            "order_reference": "missing",
                            "symbol": "MU",
                            "action": "cancel",
                            "reason": "test",
                        }
                    ],
                ),
                pretrade_snapshot=snapshot(),
                portfolio_output=portfolio_output(),
                risk_profile=risk,
                order_policy=policy,
            )
        self.assertEqual(
            plan.actions[0].status,
            OrderStatus.BLOCKED,
        )


if __name__ == "__main__":
    unittest.main()
