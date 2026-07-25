"""验证 Stage F 风险与订单策略版本。

作用：检查 paper1 引用和必需风控、券商能力字段。
重要性：防止配置漂移让订单规划退回旧风险或散落的隐式规则。
"""

from __future__ import annotations

import unittest

from v2.profiles import (
    load_order_policy,
    load_profile,
    load_risk_profile,
)


class OrderPolicyVersionTests(unittest.TestCase):
    def test_paper1_uses_stage_f_versions(self) -> None:
        profile = load_profile("paper1")
        self.assertEqual(
            profile.risk_profile,
            "paper_standard@1.1.0",
        )
        self.assertEqual(
            profile.order_policy,
            "paper_equity@1.0.0",
        )

    def test_risk_profile_has_all_order_limits(self) -> None:
        risk = load_risk_profile(
            "paper_standard@1.1.0"
        )
        for field in (
            "minimum_cash_weight",
            "maximum_single_position_weight",
            "maximum_sector_weight",
            "maximum_new_capital_per_cycle_weight",
            "maximum_order_count",
            "minimum_order_value",
            "allow_short_positions",
            "quote_max_age_seconds",
            "regular_spread_limit_bps",
            "extended_spread_limit_bps",
        ):
            self.assertIn(field, risk.settings)
        self.assertFalse(
            risk.settings["allow_short_positions"]
        )

    def test_order_policy_centralizes_capabilities(self) -> None:
        policy = load_order_policy(
            "paper_equity@1.0.0"
        )
        self.assertEqual(
            policy.settings[
                "supported_order_types"
            ]["extended_hours"],
            ["limit"],
        )
        self.assertIn(
            "broker_capabilities",
            policy.settings,
        )


if __name__ == "__main__":
    unittest.main()
