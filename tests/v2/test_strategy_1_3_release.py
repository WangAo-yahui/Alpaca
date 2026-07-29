"""验证 core_long 1.3.0 只升级 live1 并锁定长期策略模型合同。"""

from __future__ import annotations

import json
import unittest

from jsonschema import Draft202012Validator

from v2.codex.runner import codex_runner_settings
from v2.profiles import load_profile
from v2.releases import load_strategy_release
from v2.runtime import load_json_object


class StrategyOneThreeReleaseTests(unittest.TestCase):
    def test_live_only_long_horizon_release(self) -> None:
        prior = load_strategy_release(
            "core_long",
            "1.2.0",
        )
        current = load_strategy_release(
            "core_long",
            "1.3.0",
        )
        self.assertEqual(
            load_profile("live1").strategy_version,
            "1.4.0",
        )
        self.assertEqual(
            load_profile("paper1").strategy_version,
            "1.2.0",
        )
        self.assertIn(
            "config/codex_policy.json",
            current.config_hashes,
        )
        self.assertEqual(
            codex_runner_settings(current),
            {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "verbosity": "high",
            },
        )
        policy = json.loads(
            (
                current.root
                / "config"
                / "portfolio_policy.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            policy["allow_empty_portfolio"]
        )
        self.assertEqual(
            policy["target_holdings"]["minimum"],
            0,
        )
        self.assertEqual(
            policy["expected_contributions"][
                "reference_amount_cny"
            ],
            "3000",
        )
        self.assertEqual(
            policy["expected_contributions"][
                "commitment"
            ],
            "non_committed",
        )
        self.assertFalse(
            policy["expected_contributions"][
                "amount_guaranteed"
            ]
        )
        self.assertFalse(
            policy["expected_contributions"][
                "timing_guaranteed"
            ]
        )
        self.assertNotEqual(
            current.release_hash,
            prior.release_hash,
        )

    def test_stage_f_schemas_accept_versioned_releases(
        self,
    ) -> None:
        for name in (
            "proposed_orders.schema.json",
            "validated_orders.schema.json",
        ):
            with self.subTest(name=name):
                schema = load_json_object(
                    load_strategy_release(
                        "core_long",
                        "1.3.0",
                    ).root.parents[2]
                    / "schemas"
                    / "v2"
                    / name
                )
                version_schema = schema[
                    "properties"
                ]["strategy_version"]
                validator = Draft202012Validator(
                    version_schema
                )
                self.assertTrue(
                    validator.is_valid("1.2.0")
                )
                self.assertTrue(
                    validator.is_valid("1.3.0")
                )
                self.assertFalse(
                    validator.is_valid("latest")
                )


if __name__ == "__main__":
    unittest.main()
