"""覆盖 Codex 保护计划到 Alpaca SDK 请求的完整能力矩阵。

作用：验证既有持仓、新入场、碎股降级、分级 OCO、幂等保持和安全替换。
重要性：保护单属于真实卖出权限；任何组合若未通过这些 Python 硬校验都不得提交。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alpaca.trading.enums import OrderClass
from alpaca.trading.requests import (
    LimitOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
    TrailingStopOrderRequest,
)

from tests.v2.order_support import (
    ACCOUNT_HASH,
    execution_decision,
    execution_output,
    order_configs,
    order_paths,
    order_state,
    portfolio_output,
    snapshot,
)
from v2.models.orders import OrderStatus
from v2.trading.order_builder import (
    build_order_plan,
)
from v2.trading.order_request_factory import (
    build_sdk_request,
    create_request_specs,
)
from v2.trading.order_validator import (
    validate_order_plan,
)


def _plan(
    mode: str,
    *,
    apply_to: str = "existing_position",
) -> dict[str, object]:
    result: dict[str, object] = {
        "symbol": "MU",
        "mode": mode,
        "apply_to": apply_to,
        "coverage_fraction": "1",
        "time_in_force": "gtc",
        "take_profit_price": None,
        "stop_price": None,
        "stop_limit_price": None,
        "trail_price": None,
        "trail_percent": None,
        "stages": [],
        "reason": "test protection",
    }
    if mode in {
        "take_profit",
        "oco",
        "bracket",
        "oto_take_profit",
    }:
        result["take_profit_price"] = "110"
    if mode in {
        "stop",
        "stop_limit",
        "oco",
        "bracket",
        "oto_stop",
    }:
        result["stop_price"] = "90"
    if mode in {
        "stop_limit",
        "oco",
        "bracket",
    }:
        result["stop_limit_price"] = "89"
    if mode == "trailing_stop":
        result["trail_percent"] = "5"
    if mode == "staged_oco":
        result["stages"] = [
            {
                "coverage_fraction": "0.5",
                "take_profit_price": "108",
                "stop_price": "92",
                "stop_limit_price": "91",
            },
            {
                "coverage_fraction": "0.5",
                "take_profit_price": "115",
                "stop_price": "88",
                "stop_limit_price": None,
            },
        ]
    return result


def _position(quantity: str = "10") -> dict[str, str]:
    return {
        "symbol": "MU",
        "side": "long",
        "quantity": quantity,
        "available_quantity": quantity,
        "average_entry_price": "95",
        "current_price": "100",
        "market_value": str(
            float(quantity) * 100
        ),
    }


def _hold_decision() -> dict[str, object]:
    return execution_decision(
        portfolio_action="hold",
        execution_decision="defer",
        side="none",
        execution_fraction="0",
        urgency="none",
        price_condition={
            "reference": "none",
            "limit_price": None,
            "do_not_execute_above": None,
            "review_below": None,
        },
        order_intent={
            "preferred_type": "none",
            "time_in_force_preference": "none",
            "extended_hours_requested": False,
            "allow_queue": False,
            "allow_partial_fill": False,
        },
    )


class ProtectiveOrderTests(unittest.TestCase):
    def _build(
        self,
        protection: dict[str, object],
        *,
        current_snapshot=None,
        decision=None,
        allow_trade: bool = False,
    ):
        risk, policy = order_configs()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        execution = execution_output(
            decision or _hold_decision(),
            protection_plans=[protection],
        )
        current = current_snapshot or snapshot(
            positions=[_position()]
        )
        proposed = build_order_plan(
            paths=order_paths(
                Path(temporary.name)
            ),
            state=order_state(
                allow_trade=allow_trade
            ),
            execution_output=execution,
            pretrade_snapshot=current,
            portfolio_output=portfolio_output(
                "MU"
            ),
            risk_profile=risk,
            order_policy=policy,
        )
        validated = validate_order_plan(
            plan=proposed,
            execution_output=execution,
            pretrade_snapshot=current,
            risk_profile=risk,
            order_policy=policy,
            expected_account_id_hash=(
                ACCOUNT_HASH
            ),
        )
        return proposed, validated

    def test_all_existing_position_modes_map_to_supported_requests(
        self,
    ) -> None:
        expected = {
            "stop": (
                "simple",
                StopOrderRequest,
                1,
            ),
            "stop_limit": (
                "simple",
                StopLimitOrderRequest,
                1,
            ),
            "take_profit": (
                "simple",
                LimitOrderRequest,
                1,
            ),
            "trailing_stop": (
                "simple",
                TrailingStopOrderRequest,
                1,
            ),
            "oco": (
                "oco",
                LimitOrderRequest,
                1,
            ),
            "bracket": (
                "oco",
                LimitOrderRequest,
                1,
            ),
            "oto_stop": (
                "simple",
                StopOrderRequest,
                1,
            ),
            "oto_take_profit": (
                "simple",
                LimitOrderRequest,
                1,
            ),
            "staged_oco": (
                "oco",
                LimitOrderRequest,
                2,
            ),
        }
        for mode, (
            order_class,
            request_type,
            count,
        ) in expected.items():
            with self.subTest(mode=mode):
                _, validated = self._build(
                    _plan(mode)
                )
                approved = [
                    item
                    for item in validated.orders
                    if item.status
                    == OrderStatus.DRY_RUN_APPROVED
                ]
                self.assertEqual(
                    len(approved),
                    count,
                    [
                        issue.to_dict()
                        for item in validated.orders
                        for issue in item.issues
                    ],
                )
                for item in approved:
                    self.assertEqual(
                        item.order.order_class,
                        order_class,
                    )
                    request = build_sdk_request(
                        item.order
                    )
                    self.assertIsInstance(
                        request,
                        request_type,
                    )

    def test_fractional_oco_downgrades_to_day_stop_limit(
        self,
    ) -> None:
        proposed, validated = self._build(
            _plan("oco"),
            current_snapshot=snapshot(
                positions=[
                    _position("0.246692")
                ]
            ),
        )
        protective = next(
            item
            for item in validated.orders
            if item.order.protection_role
            != "none"
        )
        self.assertEqual(
            protective.status,
            OrderStatus.DRY_RUN_APPROVED,
            [
                issue.to_dict()
                for issue in protective.issues
            ],
        )
        self.assertEqual(
            protective.order.order_class,
            "simple",
        )
        self.assertEqual(
            protective.order.order_type,
            "stop_limit",
        )
        self.assertEqual(
            protective.order.time_in_force,
            "day",
        )
        self.assertTrue(
            any(
                "fractional_oco_downgraded"
                in warning
                for warning in proposed.warnings
            )
        )
        request = build_sdk_request(
            protective.order
        )
        self.assertIsInstance(
            request,
            StopLimitOrderRequest,
        )

    def test_whole_share_new_entry_attaches_bracket(
        self,
    ) -> None:
        decision = execution_decision(
            target_weight="0.002",
            maximum_weight="0.002",
            execution_fraction="1",
        )
        _, validated = self._build(
            _plan(
                "bracket",
                apply_to="new_entry",
            ),
            current_snapshot=snapshot(
                fractionable=False
            ),
            decision=decision,
        )
        approved = [
            item
            for item in validated.orders
            if item.status
            == OrderStatus.DRY_RUN_APPROVED
        ]
        self.assertEqual(len(approved), 1)
        order = approved[0].order
        self.assertEqual(
            order.order_class,
            "bracket",
        )
        request = build_sdk_request(order)
        self.assertEqual(
            request.order_class,
            OrderClass.BRACKET,
        )
        self.assertIsNotNone(
            request.take_profit
        )
        self.assertIsNotNone(
            request.stop_loss
        )

    def test_unchanged_protection_is_kept_without_hourly_churn(
        self,
    ) -> None:
        open_oco = {
            "broker_order_id": "protect-1",
            "client_order_id": (
                "wa2-paper1-cycle-pt-oco-sell-mu-a1"
            ),
            "symbol": "MU",
            "side": "sell",
            "type": "limit",
            "order_class": "oco",
            "time_in_force": "gtc",
            "quantity": "10",
            "filled_quantity": "0",
            "remaining_quantity": "10",
            "limit_price": "110",
            "stop_price": None,
            "trail_price": None,
            "trail_percent": None,
            "status": "new",
            "legs": [
                {
                    "broker_order_id": (
                        "protect-1-stop"
                    ),
                    "client_order_id": (
                        "wa2-paper1-cycle-pt-oco-stop"
                    ),
                    "symbol": "MU",
                    "side": "sell",
                    "type": "stop_limit",
                    "order_class": "oco",
                    "time_in_force": "gtc",
                    "quantity": "10",
                    "filled_quantity": "0",
                    "limit_price": "89",
                    "stop_price": "90",
                    "status": "held",
                    "legs": [],
                }
            ],
        }
        proposed, _ = self._build(
            _plan("oco"),
            current_snapshot=snapshot(
                positions=[_position()],
                open_orders=[open_oco],
            ),
        )
        self.assertFalse(proposed.actions)
        self.assertFalse(
            any(
                item.protection_role != "none"
                for item in proposed.orders
            )
        )
        self.assertIn(
            "MU:existing_protection_unchanged",
            proposed.warnings,
        )

    def test_changed_protection_requires_cancel_refresh_before_submit(
        self,
    ) -> None:
        open_stop = {
            "broker_order_id": "protect-1",
            "client_order_id": (
                "wa2-paper1-cycle-pt-stp-sell-mu-a1"
            ),
            "symbol": "MU",
            "side": "sell",
            "type": "stop",
            "order_class": "simple",
            "time_in_force": "gtc",
            "quantity": "10",
            "filled_quantity": "0",
            "remaining_quantity": "10",
            "limit_price": None,
            "stop_price": "85",
            "trail_price": None,
            "trail_percent": None,
            "status": "new",
            "legs": [],
        }
        proposed, validated = self._build(
            _plan("stop"),
            current_snapshot=snapshot(
                positions=[_position()],
                open_orders=[open_stop],
            ),
            allow_trade=True,
        )
        self.assertEqual(
            len(proposed.actions),
            1,
        )
        self.assertEqual(
            proposed.actions[0].status,
            OrderStatus.DEPENDENT,
        )
        protective = next(
            item
            for item in validated.orders
            if item.order.protection_role
            != "none"
        )
        self.assertEqual(
            protective.status,
            OrderStatus.DEPENDENT,
        )
        self.assertEqual(
            create_request_specs(validated),
            (),
        )


if __name__ == "__main__":
    unittest.main()
