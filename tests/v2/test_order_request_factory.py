"""验证 Stage F Alpaca 请求规格。

作用：覆盖 regular、扩展时段、market/limit 与 SDK 本地实例化。
重要性：请求参数必须在不接触 TradingClient 的前提下先被官方模型拒绝或接受。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
)

from v2.models.orders import OrderStatus
from v2.trading.order_builder import build_order_plan
from v2.trading.order_request_factory import (
    build_sdk_request,
    create_request_specs,
)
from v2.trading.order_validator import (
    validate_order_plan,
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


class OrderRequestFactoryTests(unittest.TestCase):
    def _validated(
        self,
        *,
        decision=None,
        snap=None,
    ):
        risk, policy = order_configs()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        execution = execution_output(
            decision or execution_decision()
        )
        current = snap or snapshot()
        plan = build_order_plan(
            paths=order_paths(Path(temp.name)),
            state=order_state(),
            execution_output=execution,
            pretrade_snapshot=current,
            portfolio_output=portfolio_output(),
            risk_profile=risk,
            order_policy=policy,
        )
        return validate_order_plan(
            plan=plan,
            execution_output=execution,
            pretrade_snapshot=current,
            risk_profile=risk,
            order_policy=policy,
            expected_account_id_hash=ACCOUNT_HASH,
        )

    def test_regular_limit_request_is_locally_validated(
        self,
    ) -> None:
        validated = self._validated()
        specs = create_request_specs(validated)
        self.assertEqual(len(specs), 1)
        self.assertEqual(
            specs[0].request_class,
            "LimitOrderRequest",
        )
        self.assertTrue(
            specs[0].local_sdk_validated
        )
        request = build_sdk_request(
            validated.orders[0].order
        )
        self.assertIsInstance(
            request,
            LimitOrderRequest,
        )

    def test_regular_market_request(self) -> None:
        decision = execution_decision(
            order_intent={
                "preferred_type": "market",
                "time_in_force_preference": "day",
                "extended_hours_requested": False,
                "allow_queue": False,
                "allow_partial_fill": True,
            }
        )
        validated = self._validated(
            decision=decision
        )
        request = build_sdk_request(
            validated.orders[0].order
        )
        self.assertIsInstance(
            request,
            MarketOrderRequest,
        )

    def test_extended_hours_requires_supported_limit(
        self,
    ) -> None:
        extended = execution_decision(
            order_intent={
                "preferred_type": "limit",
                "time_in_force_preference": "day",
                "extended_hours_requested": True,
                "allow_queue": False,
                "allow_partial_fill": True,
            }
        )
        valid = self._validated(
            decision=extended,
            snap=snapshot(
                market_phase="before_market_open"
            ),
        )
        self.assertEqual(
            valid.orders[0].status,
            OrderStatus.DRY_RUN_APPROVED,
        )
        unsupported = execution_decision(
            order_intent={
                "preferred_type": "market",
                "time_in_force_preference": "day",
                "extended_hours_requested": True,
                "allow_queue": False,
                "allow_partial_fill": True,
            }
        )
        blocked = self._validated(
            decision=unsupported,
            snap=snapshot(
                market_phase="after_market_close"
            ),
        )
        self.assertEqual(
            blocked.orders[0].status,
            OrderStatus.BLOCKED,
        )

    def test_weekend_queue_without_capability_is_blocked(
        self,
    ) -> None:
        result = self._validated(
            snap=snapshot(
                market_phase=(
                    "market_closed_weekend"
                )
            )
        )
        self.assertEqual(
            result.orders[0].status,
            OrderStatus.BLOCKED,
        )
        self.assertEqual(
            create_request_specs(result),
            (),
        )


if __name__ == "__main__":
    unittest.main()
