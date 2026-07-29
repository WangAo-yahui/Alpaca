"""验证 core_long 1.4.0 的分榜粗选、模型自主仓位和资金合同。"""

from __future__ import annotations

import unittest

from jsonschema import Draft202012Validator

from v2.codex.runner import codex_runner_settings
from v2.codex.validation import (
    preflight_output_schema,
)
from v2.profiles import load_profile
from v2.releases import load_strategy_release
from v2.runtime import load_json_object


class StrategyOneFourReleaseTests(
    unittest.TestCase
):
    def test_live_profile_and_release_contract(
        self,
    ) -> None:
        previous = load_strategy_release(
            "core_long",
            "1.3.0",
        )
        current = load_strategy_release(
            "core_long",
            "1.4.0",
        )
        self.assertEqual(
            load_profile("live1").strategy_version,
            "1.4.0",
        )
        self.assertEqual(
            load_profile("paper1").strategy_version,
            "1.2.0",
        )
        self.assertEqual(
            codex_runner_settings(current),
            {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "verbosity": "high",
            },
        )
        manifest = load_json_object(
            current.manifest_path
        )
        self.assertEqual(
            manifest["prompt_hashes"],
            dict(current.prompt_hashes),
        )
        self.assertEqual(
            manifest["schema_hashes"],
            dict(current.schema_hashes),
        )
        self.assertEqual(
            manifest["config_hashes"],
            dict(current.config_hashes),
        )
        coarse_policy = load_json_object(
            current.root
            / "config"
            / "coarse_policy.json"
        )
        self.assertEqual(
            coarse_policy[
                "python_shortlist_count"
            ],
            {"stock": 100, "etf": 20},
        )
        self.assertTrue(
            coarse_policy[
                "external_discovery"
            ][
                "score_stocks_and_etfs_separately"
            ]
        )
        portfolio_policy = load_json_object(
            current.root
            / "config"
            / "portfolio_policy.json"
        )
        objective = portfolio_policy[
            "investment_objective"
        ]
        self.assertTrue(
            objective["allow_long_term_full_cash"]
        )
        self.assertTrue(
            objective[
                "allow_long_term_fully_invested"
            ]
        )
        contributions = portfolio_policy[
            "expected_contributions"
        ]
        self.assertEqual(
            contributions["pattern"],
            "irregular_uncommitted",
        )
        self.assertNotIn(
            "reference_amount_cny",
            contributions,
        )
        self.assertEqual(
            contributions["usdt_to_usd_priority"],
            (
                "convert_before_equity_deployment_"
                "when_detected"
            ),
        )
        self.assertNotEqual(
            current.release_hash,
            previous.release_hash,
        )

    def test_new_output_schemas_are_valid_and_strict(
        self,
    ) -> None:
        release = load_strategy_release(
            "core_long",
            "1.4.0",
        )
        coarse = load_json_object(
            release.root
            / "schemas"
            / "coarse_output.schema.json"
        )
        portfolio = load_json_object(
            release.root
            / "schemas"
            / "portfolio_output.schema.json"
        )
        Draft202012Validator.check_schema(coarse)
        Draft202012Validator.check_schema(
            portfolio
        )
        preflight_output_schema(coarse)
        capital_plan = portfolio["properties"][
            "capital_deployment_plan"
        ]
        self.assertEqual(
            set(capital_plan["required"]),
            set(capital_plan["properties"]),
        )
        self.assertNotIn(
            "reference_amount_cny",
            capital_plan["properties"],
        )
        self.assertIn(
            "capital_competition",
            portfolio["required"],
        )


if __name__ == "__main__":
    unittest.main()
