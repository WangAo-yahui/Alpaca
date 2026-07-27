"""验证 Live 美股闭市排队的保守建仓规则。

作用：覆盖周末 limit/day 排队单、四日内最后报价和 25% 开仓执行比例上限。
重要性：非交易时段可以准备调仓，但不能把排队误当成交或绕过保守资金比例。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.orders import (
    OrderStatus,
    PreTradeSnapshot,
)
from v2.profiles import (
    load_order_policy,
    load_risk_profile,
)
from v2.runtime import build_cycle_paths
from v2.trading.order_builder import (
    build_order_plan,
)
from v2.trading.order_request_factory import (
    create_request_specs,
)
from v2.trading.order_validator import (
    validate_order_plan,
)
from tests.v2.order_support import (
    ACCOUNT_HASH,
    GENERATED_AT,
    execution_decision,
    execution_output,
    order_state,
    pretrade_payload,
)


class LiveClosedSessionQueueTests(
    unittest.TestCase
):
    def _validated(
        self,
        execution_fraction: str,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        paths = build_cycle_paths(
            cycle_id="20260724T140000",
            run_date="2026-07-24",
            project_root=Path(temporary.name),
            profile_id="live1",
            strategy_id="core_long",
            strategy_version="1.2.0",
        )
        risk = load_risk_profile(
            "live_full@1.0.0"
        )
        policy = load_order_policy(
            "live_equity@1.0.0"
        )
        decision = execution_decision(
            "SPY",
            target_weight="0.20",
            maximum_weight="0.30",
            execution_fraction=execution_fraction,
            order_intent={
                "preferred_type": "limit",
                "time_in_force_preference": "day",
                "extended_hours_requested": False,
                "allow_queue": True,
                "allow_partial_fill": True,
            },
        )
        execution = execution_output(decision)
        execution["profile_id"] = "live1"
        payload = pretrade_payload(
            symbols=("SPY",),
            market_phase="market_closed_weekend",
            cash="1000",
            buying_power="1000",
            portfolio_value="1000",
            quote_age="172800",
        )
        payload["profile_id"] = "live1"
        payload["order_policy"] = policy.reference
        payload["broker_capabilities"][
            "supports_closed_session_queue"
        ] = True
        snapshot = PreTradeSnapshot.from_payload(
            payload
        )
        plan = build_order_plan(
            paths=paths,
            state=order_state(),
            execution_output=execution,
            pretrade_snapshot=snapshot,
            portfolio_output={
                "decisions": [
                    {
                        "symbol": "SPY",
                        "priority": 1,
                        "conviction": "medium",
                        "sector": "ETF",
                    }
                ]
            },
            risk_profile=risk,
            order_policy=policy,
            generated_at=GENERATED_AT.isoformat(),
        )
        return validate_order_plan(
            plan=plan,
            execution_output=execution,
            pretrade_snapshot=snapshot,
            risk_profile=risk,
            order_policy=policy,
            expected_account_id_hash=ACCOUNT_HASH,
        )

    def test_conservative_weekend_open_can_queue(
        self,
    ) -> None:
        validated = self._validated("0.25")
        self.assertEqual(
            validated.orders[0].status,
            OrderStatus.DRY_RUN_APPROVED,
        )
        specs = create_request_specs(validated)
        self.assertEqual(len(specs), 1)
        self.assertEqual(
            specs[0].time_in_force,
            "day",
        )
        self.assertFalse(
            specs[0].extended_hours
        )

    def test_weekend_open_above_fraction_cap_is_blocked(
        self,
    ) -> None:
        validated = self._validated("0.50")
        self.assertEqual(
            validated.orders[0].status,
            OrderStatus.BLOCKED,
        )
        codes = {
            issue.code
            for issue in validated.global_issues
        }
        self.assertIn(
            "CLOSED_SESSION_EXECUTION_INVALID",
            codes,
        )


if __name__ == "__main__":
    unittest.main()
