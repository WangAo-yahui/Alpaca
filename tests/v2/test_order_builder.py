"""验证 Stage F 确定性订单构建。

作用：覆盖潜在暴露、执行比例、数量量化、卖出上限和多买单资本顺序。
重要性：这些测试直接防止重复资金、超卖和非确定性恢复。
"""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from v2.models.orders import OrderStatus
from v2.trading.order_builder import build_order_plan
from tests.v2.order_support import (
    GENERATED_AT,
    execution_decision,
    execution_output,
    order_configs,
    order_paths,
    order_state,
    portfolio_output,
    snapshot,
)


def _position(
    *,
    symbol: str = "MU",
    quantity: str = "10",
    available: str = "8",
    market_value: str = "1000",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "side": "long",
        "quantity": quantity,
        "available_quantity": available,
        "average_entry_price": "100",
        "market_value": market_value,
        "cost_basis": market_value,
        "unrealized_pl": "0",
        "current_price": "100",
        "lastday_price": "100",
        "change_today": "0",
    }


def _open_order(
    *,
    symbol: str = "MU",
    side: str = "buy",
    quantity: str = "10",
    filled: str = "0",
    price: str | None = "100",
) -> dict[str, object]:
    return {
        "broker_order_id": f"order-{symbol}-{side}",
        "client_order_id": f"prior-{symbol}-{side}",
        "symbol": symbol,
        "side": side,
        "type": "limit",
        "time_in_force": "day",
        "quantity": quantity,
        "filled_quantity": filled,
        "remaining_quantity": str(
            Decimal(quantity) - Decimal(filled)
        ),
        "limit_price": price,
        "status": "new",
        "extended_hours": False,
    }


class OrderBuilderTests(unittest.TestCase):
    def _build(
        self,
        *,
        decisions=None,
        snap=None,
        portfolio=None,
        actions=None,
        allow_trade=False,
    ):
        risk, policy = order_configs()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = execution_output(
            *(decisions or (execution_decision(),)),
            actions=actions,
        )
        return build_order_plan(
            paths=order_paths(Path(temp.name)),
            state=order_state(
                allow_trade=allow_trade
            ),
            execution_output=output,
            pretrade_snapshot=snap or snapshot(),
            portfolio_output=(
                portfolio or portfolio_output()
            ),
            risk_profile=risk,
            order_policy=policy,
            generated_at=GENERATED_AT.isoformat(),
        )

    def test_current_and_open_orders_form_potential_exposure(
        self,
    ) -> None:
        plan = self._build(
            snap=snapshot(
                positions=[
                    _position(
                        market_value="3000"
                    )
                ],
                open_orders=[
                    _open_order(
                        side="buy",
                        quantity="10",
                    ),
                    _open_order(
                        side="sell",
                        quantity="5",
                    ),
                ],
            )
        )
        order = plan.orders[0]
        self.assertEqual(
            order.current_position_value,
            Decimal("3000"),
        )
        self.assertEqual(
            order.open_buy_remaining_value,
            Decimal("1000"),
        )
        self.assertEqual(
            order.open_sell_remaining_value,
            Decimal("500"),
        )
        self.assertEqual(
            order.potential_position_value,
            Decimal("3500"),
        )
        self.assertEqual(
            order.execution_delta_value,
            Decimal("2250"),
        )

    def test_fractional_and_whole_share_quantization(
        self,
    ) -> None:
        fractional = self._build().orders[0]
        whole = self._build(
            snap=snapshot(
                fractionable=False
            )
        ).orders[0]
        self.assertLessEqual(
            -fractional.quantity.as_tuple().exponent,
            6,
        )
        self.assertEqual(
            whole.quantity,
            whole.quantity.to_integral_value(),
        )

    def test_minimum_order_and_execution_fraction(
        self,
    ) -> None:
        small = self._build(
            decisions=[
                execution_decision(
                    target_weight="0.0001",
                    execution_fraction="0.50",
                )
            ]
        ).orders[0]
        full = self._build(
            decisions=[
                execution_decision(
                    execution_fraction="1"
                )
            ]
        ).orders[0]
        half = self._build().orders[0]
        self.assertEqual(
            small.status,
            OrderStatus.SKIPPED,
        )
        self.assertIn(
            "below_minimum_order_value",
            small.reason_codes,
        )
        self.assertGreater(
            full.planned_value,
            half.planned_value,
        )

    def test_sell_never_exceeds_available_and_close_uses_free_qty(
        self,
    ) -> None:
        position = _position(
            quantity="10",
            available="8",
            market_value="1000",
        )
        decision = execution_decision(
            portfolio_action="close",
            side="sell",
            target_weight="0",
            execution_fraction="0.25",
            price_condition={
                "reference": "bid",
                "limit_price": "100.00",
                "do_not_execute_above": None,
                "review_below": None,
            },
        )
        order = self._build(
            decisions=[decision],
            snap=snapshot(
                positions=[position]
            ),
        ).orders[0]
        self.assertEqual(
            order.quantity,
            Decimal("8"),
        )

    def test_multi_buy_consumes_capital_in_priority_order(
        self,
    ) -> None:
        decisions = [
            execution_decision("AMD"),
            execution_decision("MU"),
        ]
        plan = self._build(
            decisions=decisions,
            snap=snapshot(
                symbols=("MU", "AMD"),
                cash="26000",
            ),
            portfolio=portfolio_output(
                "MU",
                "AMD",
            ),
        )
        by_symbol = {
            order.symbol: order
            for order in plan.orders
        }
        self.assertLessEqual(
            sum(
                (
                    item.planned_value
                    for item in plan.orders
                    if item.status
                    == OrderStatus.PROPOSED
                ),
                Decimal("0"),
            ),
            Decimal("1000"),
        )
        self.assertEqual(
            by_symbol["AMD"].status,
            OrderStatus.SKIPPED,
        )

    def test_same_input_has_stable_order_output(
        self,
    ) -> None:
        first = self._build().to_dict()
        second = self._build().to_dict()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
