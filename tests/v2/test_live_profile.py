"""验证 live1 profile、独立凭据和100%资金配置。

作用：离线证明 Live 客户端使用 paper=False 且所有版本化 policy 环境一致。
重要性：测试不得读取真实 .env_live，也不得访问或写入 Alpaca。
"""

from __future__ import annotations

import unittest

from v2.cli import parse_cli_args
from v2.data.alpaca_client import create_alpaca_clients
from v2.models.execution import (
    effective_execution_limits,
)
from v2.profiles import (
    load_order_policy,
    load_profile,
    load_risk_profile,
    load_submission_policy,
)


class LiveProfileTests(unittest.TestCase):
    def test_live_profile_contract(self) -> None:
        options = parse_cli_args(["--live"])
        profile = load_profile("live1")
        risk = load_risk_profile(
            profile.risk_profile
        )
        order = load_order_policy(
            profile.order_policy or ""
        )
        submission = load_submission_policy(
            profile.submission_policy or ""
        )
        self.assertTrue(options.live)
        self.assertFalse(options.paper)
        self.assertEqual(options.profile, "live1")
        self.assertEqual(profile.environment, "live")
        self.assertEqual(
            risk.environment,
            order.environment,
        )
        self.assertEqual(
            order.environment,
            submission.environment,
        )
        self.assertEqual(
            risk.settings["maximum_gross_exposure"],
            "1.00",
        )
        self.assertEqual(
            risk.settings["minimum_cash_weight"],
            "0.00",
        )
        self.assertEqual(
            risk.settings[
                "closed_session_quote_max_age_seconds"
            ],
            "345600",
        )
        self.assertEqual(
            risk.settings[
                "regular_equity_data_feed"
            ],
            "iex",
        )
        self.assertTrue(
            risk.settings[
                "spread_recheck_enabled"
            ]
        )
        self.assertEqual(
            risk.settings[
                (
                    "spread_recheck_required_"
                    "consecutive_passes"
                )
            ],
            3,
        )
        limits = effective_execution_limits(
            risk,
            {
                "max_single_symbol_weight": 0.15,
                "minimum_cash_weight": 0.10,
                "minimum_order_value": 25,
            },
        )
        self.assertEqual(
            limits["max_single_symbol_weight"],
            "1.00",
        )
        self.assertEqual(
            limits["minimum_cash_weight"],
            "0.00",
        )
        self.assertEqual(
            limits["minimum_order_value"],
            "1.00",
        )
        self.assertEqual(
            limits[
                "max_closed_session_quote_age_seconds"
            ],
            "345600",
        )

    def test_live_sdk_flag_and_credentials_are_profile_bound(
        self,
    ) -> None:
        profile = load_profile("live1")
        calls: list[dict[str, object]] = []

        def factory(**kwargs: object) -> object:
            calls.append(kwargs)
            return object()

        clients = create_alpaca_clients(
            paper=False,
            live=True,
            environ={
                "ALPACA_LIVE_API_KEY": "live-key",
                "ALPACA_LIVE_SECRET_KEY": "live-secret",
            },
            profile=profile,
            trading_factory=factory,
            stock_data_factory=factory,
            crypto_data_factory=factory,
        )
        self.assertTrue(clients.live)
        self.assertIs(calls[0]["paper"], False)
        self.assertEqual(calls[0]["api_key"], "live-key")
        self.assertNotIn("paper", calls[1])
        self.assertNotIn("paper", calls[2])


if __name__ == "__main__":
    unittest.main()
