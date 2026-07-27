"""验证 Live 只允许处置既有加密持仓。

作用：证明周末仍可为既有 USDT/USD 生成 GTC 卖单，同时禁止把 crypto 当美股扩展时段订单。
重要性：Live 账户中的稳定币不能锁死美股资金，也不能借此开放新的加密仓位。
"""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
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


class ExistingCryptoPositionTests(
    unittest.TestCase
):
    def test_weekend_existing_crypto_close_is_valid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = build_cycle_paths(
                cycle_id="20260724T140000",
                run_date="2026-07-24",
                project_root=root,
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
                "USDTUSD",
                portfolio_action="close",
                side="sell",
                target_weight="0",
                maximum_weight="0",
                execution_fraction="1",
                price_condition={
                    "reference": "bid",
                    "limit_price": "0.998",
                    "do_not_execute_above": None,
                    "review_below": "0.99",
                },
                order_intent={
                    "preferred_type": "market",
                    "time_in_force_preference": "gtc",
                    "extended_hours_requested": False,
                    "allow_queue": False,
                    "allow_partial_fill": True,
                },
            )
            execution = execution_output(decision)
            execution["profile_id"] = "live1"
            payload = pretrade_payload(
                symbols=("USDTUSD",),
                market_phase=(
                    "market_closed_weekend"
                ),
                positions=[
                    {
                        "symbol": "USDTUSD",
                        "side": "long",
                        "quantity": "1199.6",
                        "available_quantity": "1199.6",
                        "average_entry_price": "0.999",
                        "market_value": "1198.4",
                        "cost_basis": "1198.4",
                        "unrealized_pl": "0",
                        "current_price": "0.999",
                        "lastday_price": "0.999",
                        "change_today": "0",
                    }
                ],
                cash="29.48",
                buying_power="29.48",
                portfolio_value="1227.88",
            )
            payload["profile_id"] = "live1"
            payload["order_policy"] = (
                policy.reference
            )
            payload["assets"]["USDTUSD"].update(
                {
                    "symbol": "USDT/USD",
                    "exchange": "CRYPTO",
                    "asset_class": "crypto",
                    "overnight_tradable": False,
                    "overnight_halted": False,
                    "min_order_size": "0.1",
                    "min_trade_increment": "0.1",
                    "price_increment": "0.0001",
                }
            )
            payload["quotes"]["USDTUSD"].update(
                {
                    "bid_price": "0.998",
                    "ask_price": "1.000",
                    "midpoint": "0.999",
                    "spread": "0.002",
                    "spread_bps": "20.02",
                    "quote_age_seconds": "7200",
                }
            )
            payload["broker_capabilities"][
                "supports_crypto_24_7"
            ] = True
            snapshot = PreTradeSnapshot.from_payload(
                payload
            )
            plan = build_order_plan(
                paths=paths,
                state=order_state(
                    allow_trade=True
                ),
                execution_output=execution,
                pretrade_snapshot=snapshot,
                portfolio_output={
                    "decisions": [
                        {
                            "symbol": "USDTUSD",
                            "priority": 1,
                            "conviction": "high",
                            "sector": "crypto",
                        }
                    ]
                },
                risk_profile=risk,
                order_policy=policy,
                generated_at=(
                    GENERATED_AT.isoformat()
                ),
            )
            validated = validate_order_plan(
                plan=plan,
                execution_output=execution,
                pretrade_snapshot=snapshot,
                risk_profile=risk,
                order_policy=policy,
                expected_account_id_hash=(
                    ACCOUNT_HASH
                ),
            )
            self.assertEqual(
                validated.orders[0].status,
                OrderStatus.APPROVED,
            )
            self.assertEqual(
                validated.orders[0].order.quantity,
                Decimal("1199.6"),
            )
            self.assertEqual(
                validated.orders[0].order.limit_price,
                None,
            )
            specs = create_request_specs(validated)
            self.assertEqual(len(specs), 1)
            self.assertEqual(
                specs[0].time_in_force,
                "gtc",
            )
            self.assertEqual(
                specs[0].request_class,
                "MarketOrderRequest",
            )
            self.assertFalse(
                specs[0].extended_hours
            )


if __name__ == "__main__":
    unittest.main()
