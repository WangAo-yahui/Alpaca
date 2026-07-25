"""验证 Stage F Python 硬校验。

作用：覆盖权限状态、全局阻止、报价、资产和券商重复 ID。
重要性：确保 --allow-trade 只改变合法计划状态，不能绕过硬错误。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.orders import OrderStatus
from v2.trading.order_builder import build_order_plan
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
from tests.v2.test_order_builder import (
    _open_order,
)


class OrderValidatorTests(unittest.TestCase):
    def _validate(
        self,
        *,
        allow_trade=False,
        snap=None,
        decision=None,
    ):
        risk, policy = order_configs()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        execution = execution_output(
            decision or execution_decision()
        )
        current_snapshot = snap or snapshot()
        plan = build_order_plan(
            paths=order_paths(Path(temp.name)),
            state=order_state(
                allow_trade=allow_trade
            ),
            execution_output=execution,
            pretrade_snapshot=current_snapshot,
            portfolio_output=portfolio_output(),
            risk_profile=risk,
            order_policy=policy,
        )
        return validate_order_plan(
            plan=plan,
            execution_output=execution,
            pretrade_snapshot=current_snapshot,
            risk_profile=risk,
            order_policy=policy,
            expected_account_id_hash=ACCOUNT_HASH,
        )

    def test_dry_run_and_allow_trade_states_submit_zero(
        self,
    ) -> None:
        dry = self._validate()
        allowed = self._validate(
            allow_trade=True
        )
        self.assertEqual(
            dry.orders[0].status,
            OrderStatus.DRY_RUN_APPROVED,
        )
        self.assertEqual(
            allowed.orders[0].status,
            OrderStatus.APPROVED,
        )
        for result in (dry, allowed):
            payload = result.to_dict()
            self.assertFalse(
                payload["submission_performed"]
            )
            self.assertEqual(
                payload["submitted_order_count"],
                0,
            )

    def test_stale_quote_and_wide_spread_block(self) -> None:
        stale = self._validate(
            snap=snapshot(
                quote_age="16",
                spread_bps="31",
            )
        )
        codes = {
            issue.code
            for issue in stale.orders[0].issues
        }
        self.assertEqual(
            stale.orders[0].status,
            OrderStatus.BLOCKED,
        )
        self.assertIn(
            "QUOTE_STALE_OR_MISSING",
            codes,
        )
        self.assertIn(
            "SPREAD_LIMIT_EXCEEDED",
            codes,
        )

    def test_tradable_false_and_unknown_market_block(
        self,
    ) -> None:
        inactive = self._validate(
            snap=snapshot(tradable=False)
        )
        unknown = self._validate(
            snap=snapshot(
                market_phase="unknown"
            )
        )
        self.assertEqual(
            inactive.orders[0].status,
            OrderStatus.BLOCKED,
        )
        self.assertEqual(
            unknown.orders[0].status,
            OrderStatus.BLOCKED,
        )

    def test_global_snapshot_error_blocks_all(self) -> None:
        result = self._validate(
            snap=snapshot(ready=False)
        )
        self.assertTrue(result.global_issues)
        self.assertEqual(
            result.orders[0].status,
            OrderStatus.BLOCKED,
        )

    def test_existing_client_id_is_detected(self) -> None:
        risk, policy = order_configs()
        with tempfile.TemporaryDirectory() as temp:
            paths = order_paths(Path(temp))
            execution = execution_output()
            initial = snapshot()
            seed = build_order_plan(
                paths=paths,
                state=order_state(),
                execution_output=execution,
                pretrade_snapshot=initial,
                portfolio_output=portfolio_output(),
                risk_profile=risk,
                order_policy=policy,
            )
            existing = _open_order()
            existing["client_order_id"] = (
                seed.orders[0].client_order_id
            )
            existing["status"] = "filled"
            with_existing = snapshot(
                today_orders=[existing]
            )
            plan = build_order_plan(
                paths=paths,
                state=order_state(),
                execution_output=execution,
                pretrade_snapshot=with_existing,
                portfolio_output=portfolio_output(),
                risk_profile=risk,
                order_policy=policy,
            )
            result = validate_order_plan(
                plan=plan,
                execution_output=execution,
                pretrade_snapshot=with_existing,
                risk_profile=risk,
                order_policy=policy,
                expected_account_id_hash=(
                    ACCOUNT_HASH
                ),
            )
        self.assertIn(
            "BROKER_CLIENT_ORDER_ID_EXISTS",
            {
                issue.code
                for issue in result.orders[0].issues
            },
        )

    def test_opposite_side_open_order_is_dependent(
        self,
    ) -> None:
        result = self._validate(
            snap=snapshot(
                open_orders=[
                    _open_order(side="sell")
                ]
            )
        )
        self.assertEqual(
            result.orders[0].status,
            OrderStatus.DEPENDENT,
        )


if __name__ == "__main__":
    unittest.main()
